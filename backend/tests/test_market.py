from datetime import date, datetime, timedelta
from io import BytesIO
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import numpy as np
import pandas as pd

from app.market import _exchange_prefix, _minute_rows, _quote, analyze, fetch_bars, is_market_open, normalize_symbol
from app.main import AISettings, _format_upstream_error, market as market_endpoint
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
