from datetime import date, datetime, timedelta
from io import BytesIO
import json
import unittest
import re
from unittest.mock import patch
from urllib.error import HTTPError

import numpy as np
import pandas as pd

from app.market import _exchange_prefix, _minute_rows, _quote, analyze, fetch_bars, fetch_daily_history, is_market_open, normalize_symbol
from app.main import AISettings, BacktestInput, StrategyInput, _format_upstream_error, app, backtest as backtest_endpoint, list_strategy_templates, market as market_endpoint
from fastapi import HTTPException
from pydantic import ValidationError


class MarketAdapterTest(unittest.TestCase):
    """Verify ticker normalization and the public-feed request contract."""

    def test_exchange_is_inferred_and_explicit_suffix_is_honored(self) -> None:
        self.assertEqual(_exchange_prefix("159977"), "sz")
        self.assertEqual(_exchange_prefix("510300"), "sh")
        self.assertEqual(_exchange_prefix("000001.SH"), "sh")
        self.assertEqual(_exchange_prefix("600000.SZ"), "sz")
        self.assertEqual(normalize_symbol("159977.SZSE"), "159977")

    def test_ai_settings_reject_insecure_remote_urls(self) -> None:
        with self.assertRaises(ValidationError):
            AISettings(baseUrl="http://model.example/v1", model="model-a", apiKey="")
        local_settings = AISettings(baseUrl="http://127.0.0.1:8000/v1", model="local", apiKey="")
        self.assertTrue(local_settings.baseUrl.startswith("http://127.0.0.1"))

    def test_cors_allows_local_vite_port_but_rejects_remote_origin(self) -> None:
        middleware = next(item for item in app.user_middleware if item.cls.__name__ == "CORSMiddleware")
        origin_pattern = re.compile(middleware.kwargs["allow_origin_regex"])

        self.assertTrue(origin_pattern.fullmatch("http://127.0.0.1:5175"))
        self.assertTrue(origin_pattern.fullmatch("http://localhost:5174"))
        self.assertFalse(origin_pattern.fullmatch("https://example.com"))

    def test_strategy_payload_defaults_to_trend_filter(self) -> None:
        strategy = StrategyInput(name="已有策略")
        self.assertEqual(strategy.strategyType, "trend_filter")
        self.assertEqual(BacktestInput().strategyType, "trend_filter")

    def test_removed_strategy_is_rejected_by_save_and_backtest_inputs(self) -> None:
        """Reject old clients instead of silently mapping them to another model."""
        with self.assertRaises(ValidationError):
            StrategyInput(name="retired", strategyType="dual_ma")
        with self.assertRaises(ValidationError):
            BacktestInput(strategyType="dual_ma")

    def test_strategy_template_catalog_supplies_ui_and_scope_metadata(self) -> None:
        templates = list_strategy_templates()
        by_type = {item["strategyType"]: item for item in templates}

        self.assertEqual(set(by_type), {"trend_filter", "swing_breakout", "intraday_scalp"})
        self.assertEqual(by_type["trend_filter"]["defaultSlowPeriod"], 60)
        self.assertEqual(by_type["trend_filter"]["supportedSymbols"], ["159915", "159977", "159952", "159957", "159948"])
        self.assertEqual(by_type["trend_filter"]["supportedTimeframes"], ["daily"])
        self.assertEqual(by_type["swing_breakout"]["supportedTimeframes"], ["daily"])
        self.assertEqual(by_type["intraday_scalp"]["supportedTimeframes"], ["intraday"])

    def test_market_confirms_strategy_on_last_closed_bar_during_session(self) -> None:
        """Keep an in-progress daily candle visible without using it for the signal."""
        close = np.linspace(10, 30, 240)
        bars = pd.DataFrame({
            "datetime": pd.date_range("2025-01-01", periods=len(close), freq="D"),
            "open": close, "high": close + 0.2, "low": close - 0.2,
            "close": close, "volume": np.full(len(close), 1000.0),
        })
        metadata = {
            "source": "fixture", "sourceTime": "2025-08-28 14:00:00",
            "exchange": "SZ", "marketOpen": True,
        }
        with patch("app.main.fetch_bars", return_value=(bars, metadata)):
            result = market_endpoint(
                symbol="159977", timeframe="daily", strategyType="trend_filter",
                fastPeriod=20, slowPeriod=60,
            )

        self.assertEqual(result["strategy"]["barStatus"], "in_progress")
        self.assertEqual(result["strategy"]["asOf"], bars.iloc[-2]["datetime"].isoformat())
        self.assertIn("尚未收盘", result["strategy"]["message"])

    def test_removed_strategies_are_rejected_by_api_contract(self) -> None:
        with self.assertRaises(ValidationError):
            StrategyInput(name="retired", strategyType="growth_momentum")
        with self.assertRaises(ValidationError):
            BacktestInput(strategyType="ml_direction")

    def test_market_rejects_unknown_timeframe_as_validation_error(self) -> None:
        with self.assertRaises(HTTPException) as captured:
            market_endpoint(timeframe="nonsense")
        self.assertEqual(captured.exception.status_code, 422)

    def test_long_history_parser_uses_adjusted_daily_ohlcv_fields(self) -> None:
        payload = {
            "data": {
                "code": "159977",
                "name": "test",
                "klines": [
                    "2020-01-02,1.00,1.10,1.15,0.98,1000,12345",
                    "2020-01-03,1.10,1.05,1.12,1.01,1100,11900",
                    "2020-01-06,0,1.08,1.12,1.01,1100,11900",
                ],
            },
        }
        response = BytesIO(json.dumps(payload).encode())
        with patch("app.market.urlopen", return_value=response) as open_url:
            bars, metadata = fetch_daily_history("159977")
        self.assertIn("secid=0.159977", open_url.call_args.args[0].full_url)
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars.iloc[0]["close"], 1.10)
        self.assertEqual(metadata["barCount"], 2)
        self.assertEqual(metadata["adjustment"], "前复权")

    def test_long_history_falls_back_to_yahoo_and_adjusts_ohlc(self) -> None:
        """Use Yahoo adjusted-close factors when Eastmoney closes the connection."""
        yahoo_payload = {
            "chart": {
                "result": [{
                    "timestamp": [1_767_225_600, 1_767_312_000],
                    "indicators": {
                        "quote": [{
                            "open": [2.0, 2.1], "high": [2.2, 2.3],
                            "low": [1.9, 2.0], "close": [2.1, 2.2],
                            "volume": [1000, 1200],
                        }],
                        "adjclose": [{"adjclose": [1.05, 1.1]}],
                    },
                }],
                "error": None,
            },
        }
        yahoo_response = BytesIO(json.dumps(yahoo_payload).encode())
        eastmoney_error = HTTPError("https://eastmoney.invalid", 403, "blocked", {}, BytesIO())
        with patch("app.market.urlopen", side_effect=[eastmoney_error, yahoo_response]) as open_url:
            bars, metadata = fetch_daily_history("159977")

        self.assertEqual(open_url.call_count, 2)
        self.assertIn("query1.finance.yahoo.com", open_url.call_args.args[0].full_url)
        self.assertAlmostEqual(bars.iloc[0]["open"], 1.0)
        self.assertAlmostEqual(bars.iloc[0]["high"], 1.1)
        self.assertAlmostEqual(bars.iloc[0]["close"], 1.05)
        self.assertEqual(metadata["source"], "Yahoo Finance public adjusted daily history")
        self.assertEqual(metadata["adjustment"], "adjclose OHLC factor")

    def test_long_history_error_reports_both_failed_sources(self) -> None:
        """Keep both source failures visible when no public history feed responds."""
        eastmoney_error = HTTPError("https://eastmoney.invalid", 403, "blocked", {}, BytesIO())
        yahoo_error = HTTPError("https://yahoo.invalid", 429, "rate limited", {}, BytesIO())
        with patch("app.market.urlopen", side_effect=[eastmoney_error, yahoo_error]):
            with self.assertRaisesRegex(RuntimeError, "东方财富.*Yahoo Finance"):
                fetch_daily_history("159977")

    def test_intraday_strategy_rejects_other_symbol_or_timeframe(self) -> None:
        request = BacktestInput(symbol="000001", timeframe="weekly", strategyType="intraday_scalp")
        with self.assertRaises(HTTPException) as captured:
            backtest_endpoint(request)
        self.assertEqual(captured.exception.status_code, 422)
        self.assertIn("当日分时", captured.exception.detail)

    def test_single_symbol_backtest_returns_the_same_ohlcv_bars_used_for_signals(self) -> None:
        """Expose the exact historical candles so trade markers align with executed bars."""
        close = 20 + np.sin(np.arange(100) / 4)
        bars = pd.DataFrame({
            "datetime": pd.date_range("2025-01-01", periods=len(close), freq="D"),
            "open": close + 0.1,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "volume": np.full(len(close), 1000.0),
        })
        metadata = {"source": "fixture"}
        with patch("app.main.fetch_daily_history", return_value=(bars, metadata)):
            result = backtest_endpoint(BacktestInput(
                symbol="159977", timeframe="daily", strategyType="swing_breakout", fastPeriod=3, slowPeriod=8,
            ))

        self.assertEqual(result["symbol"], "159977")
        self.assertEqual(len(result["bars"]), len(bars))
        self.assertEqual(result["bars"][0]["datetime"], bars.iloc[0]["datetime"].strftime("%Y-%m-%d %H:%M:%S"))
        self.assertEqual(result["bars"][0]["close"], float(bars.iloc[0]["close"]))

    def test_swing_strategy_rejects_other_symbol_or_timeframe(self) -> None:
        request = BacktestInput(symbol="000001.SZ", timeframe="daily", strategyType="swing_breakout")
        with self.assertRaises(HTTPException) as captured:
            backtest_endpoint(request)
        self.assertEqual(captured.exception.status_code, 422)
        self.assertIn("创业板 ETF", captured.exception.detail)

    def test_trend_filter_uses_long_adjusted_history(self) -> None:
        """Run the long-term strategy against the history series used by its MA warmup."""
        close = 20 + np.sin(np.arange(360) / 9) + np.arange(360) * 0.005
        bars = pd.DataFrame({
            "datetime": pd.date_range("2025-01-01", periods=len(close), freq="D"),
            "open": close, "high": close + 0.2, "low": close - 0.2,
            "close": close, "volume": np.full(len(close), 1000.0),
        })
        with patch("app.main.fetch_daily_history", return_value=(bars, {"source": "fixture"})) as history, patch("app.main.fetch_bars") as short_history:
            result = backtest_endpoint(BacktestInput(
                symbol="159977", timeframe="daily", strategyType="trend_filter",
                fastPeriod=20, slowPeriod=200,
            ))

        history.assert_called_once_with("159977")
        short_history.assert_not_called()
        self.assertEqual(len(result["bars"]), len(bars))
        self.assertIn("benchmark", result)
        self.assertIn("exposurePct", result)

    def test_trend_risk_strategy_accepts_other_chinext_etfs(self) -> None:
        """Allow comparable ChiNext ETFs and reject unrelated symbols."""
        close = 20 + np.sin(np.arange(120) / 9) + np.arange(120) * 0.01
        bars = pd.DataFrame({
            "datetime": pd.date_range("2025-01-01", periods=len(close), freq="D"),
            "open": close, "high": close + 0.2, "low": close - 0.2,
            "close": close, "volume": np.full(len(close), 1000.0),
        })
        with patch("app.main.fetch_daily_history", return_value=(bars, {})):
            result = backtest_endpoint(BacktestInput(
                symbol="159915", strategyType="trend_filter", slowPeriod=60,
            ))
        self.assertEqual(result["symbol"], "159915")

        with self.assertRaises(HTTPException) as captured:
            backtest_endpoint(BacktestInput(symbol="000001", strategyType="trend_filter"))
        self.assertEqual(captured.exception.status_code, 422)

    def test_upstream_error_detail_is_visible_but_secret_is_redacted(self) -> None:
        key = "secret-test-key"
        body = b'{"error":{"message":"key secret-test-key has no access to this model"}}'
        error = HTTPError("https://provider.example/v1/chat/completions", 403, "Forbidden", {}, BytesIO(body))

        detail = _format_upstream_error(error, key)

        self.assertIn("no access to this model", detail)
        self.assertNotIn(key, detail)

    def test_market_session_excludes_weekends_and_after_hours(self) -> None:
        self.assertTrue(is_market_open(datetime(2026, 11, 2, 14, 30)))
        self.assertFalse(is_market_open(datetime(2026, 11, 2, 15, 1)))
        self.assertFalse(is_market_open(datetime(2026, 11, 1, 10, 0)))

    def test_minute_samples_exclude_lunch_and_after_close(self) -> None:
        bars = _minute_rows([
            "1130 1.01 10",
            "1131 1.02 20",
            "1200 1.03 30",
            "1300 1.04 40",
            "1500 1.05 50",
            "1501 1.06 60",
        ])
        self.assertEqual([bar["datetime"][-8:] for bar in bars], ["11:30:00", "13:00:00", "15:00:00"])

    def test_live_quote_reads_previous_close_for_daily_change(self) -> None:
        fields = ["0"] * 35
        fields[3], fields[4], fields[5], fields[6] = "11.0", "10.0", "10.5", "1000"
        fields[30], fields[33], fields[34] = "20261102103000", "11.2", "10.4"

        quote = _quote(fields)

        self.assertIsNotNone(quote)
        self.assertEqual(quote["previousClose"], 10.0)

    def test_five_day_fetch_parses_dated_sessions_and_resets_volume(self) -> None:
        sessions = []
        for offset in range(5):
            day = (date(2026, 10, 26) + timedelta(days=offset)).strftime("%Y%m%d")
            rows = [f"09{30 + index:02d} 1.{index:03d} {index + 1} 100" for index in range(20)]
            sessions.append({"date": day, "data": rows})
        payload = {"data": {"sz159977": {"data": sessions}}}

        with patch("app.market._get_json", return_value=payload) as fetch_json, patch("app.market.is_market_open", return_value=False):
            bars, metadata = fetch_bars("159977", "five_day")

        self.assertIn("/app/day/query?code=sz159977", fetch_json.call_args.args[0])
        self.assertEqual(len(bars), 100)
        self.assertEqual(bars.iloc[0]["datetime"].strftime("%Y-%m-%d %H:%M"), "2026-10-26 09:30")
        self.assertEqual(bars.iloc[20]["volume"], 1)
        self.assertTrue(metadata["sampled"])

    def test_intraday_feed_can_return_before_indicator_warmup(self) -> None:
        payload = {"data": {"sz159977": {"data": {"date": "20261102", "data": ["0930 1.0 10"]}}}}
        with patch("app.market._get_json", return_value=payload), patch("app.market.is_market_open", return_value=False):
            bars, _ = fetch_bars("159977", "intraday")
        self.assertEqual(len(bars), 1)

    def test_intraday_signal_uses_five_day_history_but_returns_today_chart(self) -> None:
        chart_bars = pd.DataFrame([{
            "datetime": pd.Timestamp("2026-11-02 09:30"),
            "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 10.0,
        }])
        history_bars = pd.DataFrame({"close": [1.0] * 80})
        metadata = {"source": "Tencent public quote", "sourceTime": "2026-11-02 09:30", "exchange": "SZ", "sampled": True}
        with patch("app.main.fetch_bars", side_effect=[(chart_bars, dict(metadata)), (history_bars, dict(metadata))]) as fetch, patch("app.main.analyze", return_value={"signal": "等待条件确认"}) as analyze_mock:
            result = market_endpoint("159977", "intraday")
        self.assertEqual(len(result["bars"]), 1)
        self.assertEqual(result["metadata"]["analysisBasis"], "最近五个交易日分钟数据")
        self.assertEqual(fetch.call_args_list[1].args, ("159977", "five_day"))
        self.assertEqual(len(analyze_mock.call_args.args[0]), 80)

    def test_intraday_strategy_uses_only_current_session_bars(self) -> None:
        bars = pd.DataFrame({
            "datetime": pd.date_range("2026-11-02 09:30", periods=30, freq="min"),
            "open": np.linspace(1, 1.1, 30), "high": np.linspace(1, 1.1, 30),
            "low": np.linspace(1, 1.1, 30), "close": np.linspace(1, 1.1, 30),
            "volume": np.full(30, 100.0),
        })
        metadata = {"source": "fixture", "sourceTime": "2026-11-02 09:59", "exchange": "SZ", "sampled": True, "marketOpen": False}
        with patch("app.main.fetch_bars", side_effect=[(bars, dict(metadata)), (bars, dict(metadata))]) as fetch, patch("app.main.analyze", return_value={"signal": "ok"}) as analysis:
            result = market_endpoint("159977", "intraday", "intraday_scalp", 5, 20)

        self.assertEqual([call.args for call in fetch.call_args_list], [("159977", "intraday"), ("159977", "five_day")])
        self.assertEqual(len(analysis.call_args.args[0]), 30)
        self.assertEqual(result["metadata"]["analysisBasis"], "当日分钟K线")
        self.assertEqual(len(result["bars"]), 30)
        self.assertTrue(result["strategy"]["supported"])

    def test_swing_backtest_uses_long_daily_history(self) -> None:
        """Route the daily swing strategy to adjusted long history, not chart samples."""
        close = 20 + np.arange(100) * 0.02
        bars = pd.DataFrame({
            "datetime": pd.date_range("2025-01-01", periods=len(close), freq="D"),
            "open": close, "high": close + 0.2, "low": close - 0.2,
            "close": close, "volume": np.full(100, 1000.0),
        })
        with patch("app.main.fetch_daily_history", return_value=(bars, {"source": "fixture"})) as history, patch("app.main.fetch_bars") as short_history:
            result = backtest_endpoint(BacktestInput(
                symbol="159977", timeframe="daily", strategyType="swing_breakout",
                fastPeriod=10, slowPeriod=20,
            ))
        history.assert_called_once_with("159977")
        short_history.assert_not_called()
        self.assertEqual(result["strategyType"], "swing_breakout")
        self.assertEqual(len(result["bars"]), len(bars))

    def test_intraday_backtest_rejects_daily_period(self) -> None:
        """Require the minute strategy to receive its own intraday sample type."""
        with self.assertRaises(HTTPException) as captured:
            backtest_endpoint(BacktestInput(
                symbol="159977", timeframe="daily", strategyType="intraday_scalp",
                fastPeriod=5, slowPeriod=20,
            ))
        self.assertEqual(captured.exception.status_code, 422)

    def test_weekly_and_monthly_requests_use_their_adjusted_rows(self) -> None:
        start = date(2020, 1, 1)
        rows = [
            [(start + timedelta(days=index * 7)).isoformat(), "1.0", "1.01", "1.02", "0.99", "1000"]
            for index in range(80)
        ]
        rows[10].append({"dividend": "2.36"})
        for timeframe, period in (("weekly", "week"), ("monthly", "month")):
            payload = {"data": {"sz159977": {f"qfq{period}": rows}}}
            with self.subTest(timeframe=timeframe), patch("app.market._get_json", return_value=payload) as fetch_json, patch("app.market.is_market_open", return_value=False):
                bars, _ = fetch_bars("159977", timeframe)
            self.assertIn(f"param=sz159977,{period},,,260,qfq", fetch_json.call_args.args[0])
            self.assertEqual(len(bars), 80)

    def test_intraday_analysis_handles_sixty_bar_warmup(self) -> None:
        closes = np.linspace(1, 2, 60)
        bars = pd.DataFrame({
            "datetime": pd.date_range("2026-11-02 09:30", periods=60, freq="min"),
            "open": closes, "high": closes + 0.01, "low": closes - 0.01,
            "close": closes, "volume": np.full(60, 100.0),
        })

        result = analyze(bars)

        self.assertFalse(result["breakout"])
        self.assertTrue(np.isfinite(result["sma60"]))

    @patch("app.market._get_json")
    def test_daily_request_has_no_invalid_space_and_normalizes_rows(self, fetch_json) -> None:
        start = date(2026, 1, 1)
        rows = [
            [(start + timedelta(days=index)).isoformat(), "1.0", "1.01", "1.02", "0.99", "1000"]
            for index in range(80)
        ]
        fetch_json.return_value = {"data": {"sz159977": {"qfqday": rows}}}

        bars, metadata = fetch_bars("159977.SZ", "daily")

        request_url = fetch_json.call_args.args[0]
        self.assertNotIn(" ", request_url)
        self.assertIn("param=sz159977,day,,,260,qfq", request_url)
        self.assertEqual(len(bars), 80)
        self.assertEqual(metadata["exchange"], "SZ")


if __name__ == "__main__":
    unittest.main()
