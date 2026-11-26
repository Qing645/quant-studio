"""Verify the replacement channel model across live signals, execution and HTTP contracts."""

import json
import unittest
from datetime import datetime
from urllib.parse import urlencode
from unittest.mock import patch

import numpy as np
import pandas as pd

from app.engine import analyze_strategy, run_backtest
from app.ai_context import CHINA_TZ
from app.star50 import Star50ChannelSignals, channel_intraday_preview
from app.strategies import normalize_strategy_parameters
from tests.test_strategy_parameters import LocalStrategyAPITest


def channel_bars() -> pd.DataFrame:
    """Create a SMA200-qualified ordinary-volume breakout and a channel exit above its ATR stop."""
    close = np.r_[np.linspace(9.5, 10., 200), np.full(70, 10.), np.full(60, 10.49)]
    close[270], close[271:282], close[282:] = 10.5, 10.49, 10.47
    return pd.DataFrame({"datetime": pd.date_range("2025-01-01", periods=len(close), freq="B"),
                         "open": close, "high": close + .01, "low": close - .01,
                         "close": close, "volume": np.full(len(close), 1000.)})


class ChannelTest(unittest.TestCase):
    """Protect the rule change: prior channels, next-open fills, trend holding and causal sizing."""

    def test_preview_explains_price_and_partial_volume_without_a_confirmed_signal(self) -> None:
        """Today's cumulative volume may be incomplete even after its price breaks out."""
        bars = channel_bars().iloc[:271].copy()
        bars.loc[270, "volume"] = 500
        settings = normalize_strategy_parameters("star50_channel")
        preview = channel_intraday_preview(bars, 10, 15, settings, "2026-11-24 10:00:00")
        self.assertTrue(preview["ready"])
        self.assertFalse(preview["entryCondition"])
        self.assertTrue(preview["details"]["breakoutPassed"])
        self.assertLess(preview["details"]["breakoutGapPct"], 0)
        self.assertGreater(preview["details"]["exitBufferPct"], 0)
        self.assertEqual(preview["details"]["volumeRatio"], .5)
        self.assertEqual(preview["blockers"], ["累计量尚未达到日量门槛"])
        self.assertIn("不能判定全天缩量", preview["message"])
        json.dumps(preview, allow_nan=False)

    def test_preview_threshold_equality_and_warmup_are_not_misreported(self) -> None:
        """Touching a channel boundary is not a strict crossing, and missing warmup is not a failure."""
        bars = channel_bars().iloc[:271].copy()
        settings = normalize_strategy_parameters("star50_channel")
        boundary = bars.high.iloc[255:270].max()
        bars.loc[270, ["open", "high", "low", "close"]] = [boundary, boundary + .01, boundary - .01, boundary]
        preview = channel_intraday_preview(bars, 10, 15, settings, "2026-11-24 10:00:00")
        self.assertAlmostEqual(preview["details"]["breakoutGapPct"], 0)
        self.assertFalse(preview["details"]["breakoutPassed"])
        self.assertFalse(preview["entryCondition"])
        boundary = bars.low.iloc[260:270].min()
        bars.loc[270, ["open", "high", "low", "close"]] = [boundary, boundary + .01, boundary - .01, boundary]
        preview = channel_intraday_preview(bars, 10, 15, settings, "2026-11-24 10:00:00")
        self.assertAlmostEqual(preview["details"]["exitBufferPct"], 0)
        self.assertFalse(preview["exitCondition"])
        preview = channel_intraday_preview(bars.iloc[:30], 10, 15, settings, "2026-11-24 10:00:00")
        self.assertFalse(preview["ready"])
        self.assertIsNone(preview["details"])
        self.assertEqual(preview["blockers"], [])
        self.assertIn("预热", preview["message"])
        json.dumps(preview, allow_nan=False)

    def test_prior_channels_drive_live_entry_and_next_open_exit(self) -> None:
        """Require baseline volume confirmation and keep trend exits independent of SMA5 recovery."""
        bars = channel_bars()
        params = normalize_strategy_parameters("star50_channel")
        signals = Star50ChannelSignals(bars, 10, 20, params)
        self.assertFalse(signals.ready[:199].any())
        self.assertAlmostEqual(signals.prior_high[270], bars.high.iloc[250:270].max())
        self.assertTrue(signals.trend[270])
        self.assertTrue(signals.trend_rising[270])
        self.assertTrue(signals.entry[270])
        quiet_breakout = bars.copy()
        quiet_breakout.loc[270, "volume"] = 999
        quiet = Star50ChannelSignals(quiet_breakout, 10, 20, params)
        self.assertTrue(quiet.breakout[270])
        self.assertFalse(quiet.volume_confirmed[270])
        self.assertFalse(quiet.entry[270])
        declining = np.r_[np.full(200, 10.), np.linspace(9.8, 9.2, 59), 9.5]
        weak_regime = pd.DataFrame({"datetime": pd.date_range("2026-01-01", periods=260, freq="B"),
                                    "open": declining, "high": declining + .01,
                                    "low": declining - .01, "close": declining,
                                    "volume": np.full(260, 1000.)})
        denied = Star50ChannelSignals(weak_regime, 10, 20, params)
        self.assertTrue(denied.ready[259])
        self.assertFalse(denied.trend[259])
        self.assertTrue(denied.breakout[259])
        self.assertFalse(denied.entry[259])
        self.assertFalse(signals.exit[271:282].any())
        self.assertTrue(signals.exit[282])
        live = analyze_strategy(bars.iloc[:271], "star50_channel", 10, 20, "588080", "daily")
        self.assertTrue(live["entryCondition"])
        self.assertTrue(live["riskReference"]["trendPassed"])
        self.assertTrue(live["riskReference"]["trendSlopePassed"])
        self.assertTrue(live["riskReference"]["volumePassed"])
        self.assertAlmostEqual(live["riskReference"]["volumeRatio"], 1.0)
        self.assertEqual(live["parameters"]["trendFilterPeriod"], 200)
        self.assertEqual(live["parameters"]["trendSlopeBars"], 20)
        self.assertEqual(live["parameters"]["volumeMultiplier"], 1)
        self.assertFalse(live["positionRiskAvailable"])
        result = run_backtest(bars, 100000, .0003, 5, 10, 20, "star50_channel")
        self.assertEqual(result["executions"][0]["time"], bars.datetime.iloc[271].isoformat())
        self.assertEqual(result["trades"][0]["exit"], bars.datetime.iloc[283].isoformat())
        self.assertEqual(result["trades"][0]["exitReason"], "跌破前10根低点退出")
        self.assertFalse(result["researchTarget"]["achieved"])
        self.assertEqual(result["researchTarget"]["annualReturnPct"], 15)
        json.dumps(result, allow_nan=False)

    def test_future_changes_do_not_rewrite_fills_and_four_percent_risk(self) -> None:
        """The signal ATR freezes sizing; opening-day extremes and later prices cannot change entry."""
        bars = channel_bars()
        before = run_backtest(bars, 10000, .0003, 5, 10, 20, "star50_channel")
        bars.loc[320, "high"] = 150
        after = run_backtest(bars, 10000, .0003, 5, 10, 20, "star50_channel")
        entry = before["executions"][0]
        self.assertEqual(entry, after["executions"][0])
        self.assertEqual(entry["shares"] % 100, 0)
        self.assertLessEqual(entry["plannedPriceRisk"], 400)
        self.assertLessEqual(entry["shares"] * entry["price"], 9000)
        self.assertGreaterEqual(entry["cash"], 0)
        bars = channel_bars()
        bars.loc[320:, ["open", "high", "low", "close"]] *= 1.3
        changed = run_backtest(bars, 10000, .0003, 5, 10, 20, "star50_channel")
        self.assertEqual(before["equityCurve"][:320], changed["equityCurve"][:320])
        with self.assertRaises(ValueError):
            normalize_strategy_parameters("star50_channel", {"rsiThreshold": 10})

    def test_channel_exit_remains_pending_until_sell_is_available(self) -> None:
        """T+1 and an unavailable sell cannot discard a previously confirmed channel exit."""
        bars = channel_bars()
        bars["sellExecutable"] = True
        bars.loc[283, "sellExecutable"] = False
        result = run_backtest(bars, 100000, 0, 0, 10, 20, "star50_channel")
        self.assertEqual(result["trades"][0]["exit"], bars.datetime.iloc[284].isoformat())
        self.assertEqual(result["trades"][0]["exitReason"], "跌破前10根低点退出")


class ChannelHTTPTest(LocalStrategyAPITest):
    """Exercise the actual HTTP routes with isolated strategy storage and a declared OHLC fixture."""

    def test_current_day_details_keep_official_signal_and_reject_invalid_parameters(self) -> None:
        """Serve today's detailed observation without changing yesterday's official conditions."""
        bars = channel_bars().iloc[:271].copy()
        bars.loc[270, "datetime"] = pd.Timestamp(datetime.now(CHINA_TZ).date())
        metadata = {"marketOpen": True, "sourceTime": f"{datetime.now(CHINA_TZ).date()} 10:30:00"}
        query = urlencode({"symbol": "588080", "timeframe": "daily", "strategyType": "star50_channel",
                           "fastPeriod": 10, "slowPeriod": 15})
        official = analyze_strategy(bars.iloc[:-1], "star50_channel", 10, 15, "588080", "daily")
        with patch("app.main.fetch_bars", return_value=(bars, metadata)), patch(
            "app.main.completed_custom_bars", return_value=(bars.iloc[:-1], True)
        ):
            status, response = self.request("/api/market?" + query)
        self.assertEqual(status, 200, response)
        for key in ("entryCondition", "exitCondition", "signal", "asOf"):
            self.assertEqual(response["strategy"][key], official[key])
        preview = response["strategy"]["intradayPreview"]
        self.assertTrue(preview["entryCondition"])
        self.assertEqual(preview["blockers"], [])
        self.assertEqual(preview["asOf"], metadata["sourceTime"])
        self.assertLess(preview["details"]["breakoutGapPct"], 0)
        self.assertEqual(self.request("/api/market?" + query + "&parameters=%7B%22volumeMultiplier%22%3A0%7D")[0], 422)

    def test_saved_model_runs_and_invalid_scope_or_old_parameters_are_rejected(self) -> None:
        """Saving a new identity must change live/backtest behavior while preserving the old record."""
        _, old = self.request("/api/strategies", {"name": "回撤旧版", "strategyType": "star50_pullback"})
        status, saved = self.request("/api/strategies", {"name": "通道新版", "strategyType": "star50_channel"})
        self.assertEqual(status, 200)
        self.assertEqual((saved["fastPeriod"], saved["slowPeriod"]), (10, 15))
        self.assertEqual(saved["parameters"]["riskBudgetPct"], 4)
        self.assertEqual(saved["parameters"]["trendFilterPeriod"], 200)
        self.assertEqual(saved["parameters"]["trendSlopeBars"], 20)
        self.assertEqual(saved["parameters"]["volumeMultiplier"], 1)
        self.assertEqual(saved["parameters"]["trailingAtrMultiplier"], 2)
        live_bars = channel_bars()
        with patch("app.main.fetch_daily_history", return_value=(channel_bars(), {})), patch(
            "app.main.fetch_bars", return_value=(live_bars.iloc[:271], {"marketOpen": False})
        ):
            status, result = self.request("/api/backtest", {"symbol": "588080", "strategyType": "star50_channel", "initialCapital": 10000})
            self.assertEqual(status, 200, result)
            self.assertEqual(result["executions"][0]["reason"], "价格通道突破入场")
            status, live = self.request("/api/market?" + urlencode({"symbol": "588080", "timeframe": "daily", "strategyType": "star50_channel"}))
            self.assertEqual(status, 200, live)
            self.assertTrue(live["strategy"]["entryCondition"])
            self.assertIn("priorExitLow", live["strategy"]["riskReference"])
            self.assertTrue(live["strategy"]["riskReference"]["trendPassed"])
            self.assertTrue(live["strategy"]["riskReference"]["trendSlopePassed"])
            self.assertTrue(live["strategy"]["riskReference"]["volumePassed"])
        for payload in ({"symbol": "159977"}, {"symbol": "588080", "timeframe": "intraday"},
                        {"symbol": "588080", "parameters": {"rsiThreshold": 10}},
                        {"symbol": "588080", "parameters": {"trendFilterPeriod": 90}},
                        {"symbol": "588080", "parameters": {"trendSlopeBars": 10}},
                        {"symbol": "588080", "parameters": {"volumeMultiplier": 3}},
                        {"symbol": "588080", "fastPeriod": 20, "slowPeriod": 10}):
            self.assertEqual(self.request("/api/backtest", {"strategyType": "star50_channel", **payload})[0], 422)
        self.assertEqual(next(item for item in self.request("/api/strategies")[1] if item["id"] == old["id"]), old)
