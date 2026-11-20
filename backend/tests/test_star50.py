"""Behavioral tests for confirmed 588080 signals, ATR state and actual local HTTP contracts."""

import json
import unittest
from urllib.parse import urlencode
from unittest.mock import patch

import numpy as np
import pandas as pd

from app.engine import analyze_strategy, run_backtest
from app.star50 import ATRPosition, Star50Signals
from app.strategies import normalize_strategy_parameters
from tests.test_strategy_parameters import LocalStrategyAPITest


def fixture() -> pd.DataFrame:
    """Create one clear breakout after warmup, with no repeated qualifying volume thereafter."""
    close = 10 + np.arange(80) * .01
    close[70:] = 11.4
    close[70] = 11.3
    volume = np.full(80, 1000.)
    volume[70] = 2000.
    return pd.DataFrame({"datetime": pd.date_range("2024-01-02", periods=80, freq="B"),
                         "open": close.copy(), "high": close + .05, "low": close - .05,
                         "close": close, "volume": volume})


def simulate(bars: pd.DataFrame, capital: float = 100000, fee: float = 0, slippage: float = 0,
             parameters: dict | None = None) -> dict:
    """Enter via the public dispatcher, with the same settings accepted by the API."""
    return run_backtest(bars, capital, fee, slippage, 20, 60, "star50_breakout", parameters=parameters,
                        _include_cost_sensitivity=False)


class Star50Test(unittest.TestCase):
    """Protect causality, frozen risk, integer sizing, T+1 and unavailable fills."""

    def test_entry_uses_prior_windows_and_shared_live_signals(self) -> None:
        """A real breakout must be possible, and a current-bar high cannot define its own gate."""
        data = fixture()
        params = normalize_strategy_parameters("star50_breakout")
        signals = Star50Signals(data, 20, 60, params)
        self.assertTrue(signals.entry[70])
        self.assertAlmostEqual(signals.prior_high[70], data.high.iloc[50:70].max())
        live = analyze_strategy(data.iloc[:71], "star50_breakout", 20, 60, "588080", "daily")
        self.assertTrue(live["entryCondition"])
        self.assertEqual(live["action"], "observe")
        self.assertFalse(live["positionRiskAvailable"])
        self.assertEqual(simulate(data)["executions"][0]["time"], data.datetime.iloc[71].isoformat())
        data.loc[70, "volume"] = 1199
        self.assertFalse(simulate(data)["openPosition"])

    def test_signal_day_atr_is_frozen_and_size_obeys_three_caps(self) -> None:
        """Execution-day intrabar volatility cannot change the opening size or initial risk line."""
        data = fixture()
        ordinary = simulate(data, fee=.0003, slippage=5)
        data.loc[71, "high"] = 40
        shocked = simulate(data, fee=.0003, slippage=5)
        first = ordinary["executions"][0]
        self.assertEqual(first, shocked["executions"][0])
        self.assertEqual(first["shares"] % 100, 0)
        self.assertLessEqual(first["plannedPriceRisk"], 500)
        self.assertLessEqual(first["shares"] * first["price"], 50000)
        self.assertGreaterEqual(first["cash"], 0)
        self.assertGreaterEqual(first["price"], first["rawPrice"])

    def test_risk_budget_changes_size_and_small_account_skips(self) -> None:
        """Budget parameters must affect actual lots; insufficient cash must not create fractions."""
        low = simulate(fixture(), parameters={"riskBudgetPct": .1})
        high = simulate(fixture(), parameters={"riskBudgetPct": 1})
        self.assertLess(low["executions"][0]["shares"], high["executions"][0]["shares"])
        small = simulate(fixture(), capital=100)
        self.assertFalse(small["executions"])
        self.assertIn("100份", small["riskManagement"]["cancelledEntries"][0]["reason"])

    def test_stop_never_loosens_as_atr_expands(self) -> None:
        """Position state must retain the initial line and the highest completed held close."""
        position = ATRPosition(None, 8, 8)
        self.assertFalse(position.advance(12, .5, 3))
        self.assertEqual(position.stop, 10.5)
        self.assertFalse(position.advance(12, 4, 3))
        self.assertEqual(position.stop, 10.5)
        self.assertTrue(position.advance(10.4, 4, 3))

    def test_buy_day_exit_is_next_session_and_can_exceed_budget(self) -> None:
        """Closing risk must never sell newly bought shares that same day or promise a stop fill."""
        data = fixture()
        data.loc[71, ["close", "low"]] = [10.0, 9.95]
        data.loc[72, ["open", "low"]] = [9.0, 8.95]
        result = simulate(data)
        trade = result["trades"][0]
        self.assertEqual(trade["entry"], data.datetime.iloc[71].isoformat())
        self.assertEqual(trade["exit"], data.datetime.iloc[72].isoformat())
        self.assertEqual(trade["exitReason"], "ATR风险线退出")
        self.assertEqual(trade["exitPrice"], 9)
        self.assertGreater(-trade["pnl"], 500)

    def test_pending_exit_survives_recovery_and_blocked_quotes(self) -> None:
        """A failed protective sell stays pending, even after price recovers or an entry appears."""
        data = fixture()
        data.loc[71, ["close", "low"]] = [10.0, 9.95]
        data["sellExecutable"] = True
        data.loc[72:73, "sellExecutable"] = False
        data.loc[73, ["open", "close", "high", "low", "volume"]] = [13, 13, 13.1, 12.9, 4000]
        result = simulate(data)
        self.assertEqual(result["trades"][0]["exit"], data.datetime.iloc[74].isoformat())
        self.assertEqual([fill["side"] for fill in result["executions"]], ["buy", "sell"])
        self.assertIsNotNone(result["riskManagement"]["riskLineCurve"][73]["pendingExit"])

    def test_unexecuted_entry_expires_and_pending_last_exit_remains_open(self) -> None:
        """Do not defer failed buys or liquidate the last candle using a fabricated next open."""
        data = fixture()
        data["buyExecutable"] = True
        data.loc[71, "buyExecutable"] = False
        self.assertFalse(simulate(data)["executions"])
        data = fixture().iloc[:72].copy()
        data.loc[71, ["close", "low"]] = [10.0, 9.95]
        result = simulate(data)
        self.assertTrue(result["openPosition"])
        self.assertEqual(result["tradeCount"], 0)
        self.assertEqual(result["riskManagement"]["pendingExit"], "ATR风险线退出")

    def test_gap_cancellation_and_zero_volume_do_not_fill(self) -> None:
        """Entry opening checks must cancel rather than move the trade to tomorrow."""
        for field, value in (("open", 13), ("volume", 0)):
            with self.subTest(field=field):
                data = fixture()
                data.loc[71, field] = value
                if field == "open":
                    data.loc[71, "high"] = 13.1
                result = simulate(data)
                self.assertFalse(result["executions"])
                self.assertEqual(len(result["riskManagement"]["cancelledEntries"]), 1)

    def test_directional_limit_gap_defers_exit_until_executable_open(self) -> None:
        """Protective exits survive a down-limit opening rather than claiming a stop-price fill."""
        data = fixture()
        data.loc[71, ["close", "low"]] = [10.0, 9.95]
        data.loc[72, ["open", "low"]] = [8.0, 7.95]
        result = simulate(data)
        self.assertEqual(result["trades"][0]["exit"], data.datetime.iloc[73].isoformat())
        self.assertIsNotNone(result["riskManagement"]["riskLineCurve"][72]["pendingExit"])

    def test_prior_signal_is_not_reused_after_same_open_exit(self) -> None:
        """An available new breakout cannot buy at the same opening used for a pending exit."""
        data = fixture()
        data.loc[71, ["close", "low"]] = [10.0, 9.95]
        data["sellExecutable"] = True
        data.loc[72:73, "sellExecutable"] = False
        data.loc[73, ["open", "close", "high", "low", "volume"]] = [13, 13, 13.1, 12.9, 4000]
        signals = Star50Signals(data, 20, 60, normalize_strategy_parameters("star50_breakout"))
        self.assertTrue(signals.entry[73])
        self.assertEqual([item["side"] for item in simulate(data)["executions"]], ["buy", "sell"])

    def test_two_own_moving_averages_confirm_exit(self) -> None:
        """SMA exits require two closes, while a wide ATR line isolates this separate rule."""
        data = fixture()
        data.loc[75:, ["open", "close", "high", "low"]] = [10.8, 10.8, 10.9, 10.7]
        result = simulate(data, parameters={"initialAtrMultiplier": 10, "trailingAtrMultiplier": 10})
        self.assertEqual(result["trades"][0]["exitReason"], "连续两根均线退出")
        self.assertEqual(result["trades"][0]["exit"], data.datetime.iloc[77].isoformat())

    def test_future_changes_do_not_change_prior_executions_or_equity(self) -> None:
        """Every simulated prefix must remain identical when future prices and volume change."""
        data = fixture()
        original = simulate(data)
        data.loc[76:, ["open", "high", "low", "close"]] *= 1.5
        data.loc[76:, "volume"] *= 4
        changed = simulate(data)
        self.assertEqual(original["equityCurve"][:76], changed["equityCurve"][:76])
        self.assertEqual(original["riskManagement"]["riskLineCurve"][:76], changed["riskManagement"]["riskLineCurve"][:76])
        self.assertEqual(original["executions"][0], changed["executions"][0])

    def test_warmup_benchmarks_costs_and_json_safety(self) -> None:
        """Expose missing warmup, both cash-aware baselines and rerun cost scenarios without NaN."""
        short = simulate(fixture().iloc[:30])
        self.assertFalse(short["executions"])
        self.assertIn("预热", short["sampleWarning"])
        result = run_backtest(fixture(), 100000, .0003, 5, 20, 60, "star50_breakout")
        self.assertEqual(len(result["benchmarks"]), 2)
        self.assertEqual(len(result["costSensitivity"]), 2)
        self.assertEqual(len(result["researchPeriods"]), 1)
        self.assertGreater(result["riskManagement"]["totalTransactionCost"], 0)
        json.dumps(result, allow_nan=False)

    def test_scope_and_invalid_daily_contract(self) -> None:
        """Reject the wrong symbol/timeframe and intraday candles in the daily-only simulator."""
        self.assertFalse(analyze_strategy(fixture(), "star50_breakout", 20, 60, "588000", "daily")["supported"])
        self.assertFalse(analyze_strategy(fixture(), "star50_breakout", 20, 60, "588080", "intraday")["supported"])
        data = fixture()
        data["datetime"] = pd.date_range("2024-01-02", periods=80, freq="min")
        with self.assertRaisesRegex(ValueError, "日K"):
            simulate(data)


class Star50HTTPTest(LocalStrategyAPITest):
    """Verify saved parameters, actual HTTP execution, rejection and incomplete-bar handling."""

    def test_save_live_backtest_and_errors(self) -> None:
        """One saved record must preserve the complete execution contract through both APIs."""
        original = {"name": "原策略", "strategyType": "trend_filter"}
        self.assertEqual(self.request("/api/strategies", original)[0], 200)
        status, saved = self.request("/api/strategies", {"name": "科创50", "strategyType": "star50_breakout"})
        self.assertEqual(status, 200)
        self.assertEqual(saved["parameters"]["riskBudgetPct"], .5)
        self.assertEqual(len(self.request("/api/strategies")[1]), 2)
        query = urlencode({"symbol": "588080", "timeframe": "daily", "strategyType": "star50_breakout"})
        with patch("app.main.fetch_bars", return_value=(fixture(), {"marketOpen": False})), patch("app.main.fetch_daily_history", return_value=(fixture(), {})):
            status, live = self.request("/api/market?" + query)
            self.assertEqual(status, 200)
            self.assertFalse(live["strategy"]["positionRiskAvailable"])
            status, result = self.request("/api/backtest", {"symbol": "588080", "strategyType": "star50_breakout"})
            self.assertEqual(status, 200)
            self.assertIn("riskManagement", result)
        for payload in ({"symbol": "159977"}, {"symbol": "588080", "timeframe": "intraday"},
                        {"symbol": "588080", "parameters": {"riskBudgetPct": 0}},
                        {"symbol": "588080", "parameters": {"slopeBars": 1.5}}):
            self.assertEqual(self.request("/api/backtest", {"strategyType": "star50_breakout", **payload})[0], 422)

    def test_live_excludes_incomplete_candle_even_when_market_is_not_open(self) -> None:
        """Lunch break must not turn today's unfinished daily candle into a confirmed signal."""
        data = fixture().iloc[:72]
        with patch("app.main.fetch_bars", return_value=(data, {"marketOpen": False})), patch(
            "app.main.completed_custom_bars", return_value=(data.iloc[:-1], True)
        ) as completed:
            status, result = self.request("/api/market?" + urlencode({"symbol": "588080", "timeframe": "daily", "strategyType": "star50_breakout"}))
        self.assertEqual(status, 200)
        completed.assert_called_once()
        self.assertEqual(result["strategy"]["barStatus"], "in_progress")
        self.assertEqual(result["strategy"]["asOf"], data.datetime.iloc[-2].isoformat())
