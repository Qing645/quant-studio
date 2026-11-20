"""Verify that the connected pullback model actually changes saved, live and backtest behavior."""

import json
from urllib.parse import urlencode
from unittest.mock import patch

from app.engine import analyze_strategy, run_backtest
from app.main import BacktestInput
from app.star50 import Star50PullbackSignals
from app.strategies import normalize_strategy_parameters
from tests.test_star50_research import pullback_bars
from tests.test_strategy_parameters import LocalStrategyAPITest
import unittest


class PullbackTest(unittest.TestCase):
    """Protect next-open causality, no fictitious holdings and the model-specific exit."""

    def test_live_entry_and_actual_simulation_use_the_same_new_rule(self) -> None:
        """A pullback with ordinary volume must buy next open and exit after mean recovery."""
        bars = pullback_bars()
        live = analyze_strategy(bars.iloc[:221], "star50_pullback", 5, 200, "588080", "daily")
        self.assertTrue(live["entryCondition"])
        self.assertTrue(live["riskReference"]["oversoldPassed"])
        self.assertNotIn("breakoutPassed", live["riskReference"])
        self.assertFalse(live["positionRiskAvailable"])
        result = run_backtest(bars, 10000, .0003, 5, 5, 200, "star50_pullback")
        self.assertEqual(result["executions"][0]["time"], bars.datetime.iloc[221].isoformat())
        self.assertEqual(result["trades"][0]["exit"], bars.datetime.iloc[224].isoformat())
        self.assertEqual(result["trades"][0]["exitReason"], "恢复至SMA5退出")
        self.assertEqual(result["parameters"]["riskBudgetPct"], 2)
        self.assertFalse(result["researchTarget"]["achieved"])
        self.assertEqual(len(result["costSensitivity"]), 2)
        json.dumps(result, allow_nan=False)

    def test_new_rule_warmup_and_legacy_contract_are_distinct(self) -> None:
        """Long indicators need 200 bars; old parameters remain valid only for the old model."""
        data = pullback_bars()
        params = normalize_strategy_parameters("star50_pullback")
        signals = Star50PullbackSignals(data, 5, 200, params)
        self.assertFalse(signals.ready[:199].any())
        with self.assertRaises(ValueError):
            normalize_strategy_parameters("star50_pullback", {"volumeMultiplier": 1.2})
        original = BacktestInput(strategyType="star50_breakout")
        self.assertEqual(original.parameters["riskBudgetPct"], .5)
        self.assertEqual(original.slowPeriod, 60)
        new = BacktestInput(strategyType="star50_pullback")
        self.assertEqual((new.fastPeriod, new.slowPeriod), (5, 200))

    def test_no_future_price_rewrites_opening_fills_and_sizing(self) -> None:
        """ATR and fills use information available at their actual decision times."""
        bars = pullback_bars()
        original = run_backtest(bars, 10000, .0003, 5, 5, 200, "star50_pullback")
        bars.loc[240:, ["open", "high", "low", "close"]] *= 1.5
        changed = run_backtest(bars, 10000, .0003, 5, 5, 200, "star50_pullback")
        self.assertEqual(original["equityCurve"][:240], changed["equityCurve"][:240])
        self.assertEqual(original["executions"][0], changed["executions"][0])
        self.assertEqual(original["executions"][0]["shares"] % 100, 0)
        self.assertLessEqual(original["executions"][0]["plannedPriceRisk"], 200)


class PullbackHTTPTest(LocalStrategyAPITest):
    """Exercise real HTTP requests while isolating the user's strategy store."""

    def test_saved_rule_roundtrips_to_live_and_backtest_and_rejects_invalid_inputs(self) -> None:
        """The saved contract must run the new model, without silently changing the old identity."""
        old_status, old = self.request("/api/strategies", {"name": "旧版", "strategyType": "star50_breakout"})
        self.assertEqual(old_status, 200)
        status, saved = self.request("/api/strategies", {"name": "新版", "strategyType": "star50_pullback"})
        self.assertEqual(status, 200)
        self.assertEqual(saved["parameters"]["riskBudgetPct"], 2)
        self.assertEqual(len(self.request("/api/strategies")[1]), 2)
        with patch("app.main.fetch_daily_history", return_value=(pullback_bars(), {})), patch(
            "app.main.fetch_bars", return_value=(pullback_bars().iloc[:221], {"marketOpen": False})
        ):
            status, result = self.request("/api/backtest", {"symbol": "588080", "strategyType": "star50_pullback", "initialCapital": 10000})
            self.assertEqual(status, 200)
            self.assertEqual(result["strategyType"], saved["strategyType"])
            self.assertIn("researchTarget", result)
            status, live = self.request("/api/market?" + urlencode({"symbol": "588080", "timeframe": "daily", "strategyType": "star50_pullback"}))
            self.assertEqual(status, 200)
            self.assertTrue(live["strategy"]["entryCondition"])
            self.assertEqual(live["strategy"]["slowPeriod"], 200)
        for payload in ({"symbol": "159977"}, {"symbol": "588080", "timeframe": "intraday"},
                        {"symbol": "588080", "parameters": {"rsiThreshold": 0}}):
            self.assertEqual(self.request("/api/backtest", {"strategyType": "star50_pullback", **payload})[0], 422)
        self.assertEqual(old["parameters"]["riskBudgetPct"], .5)
