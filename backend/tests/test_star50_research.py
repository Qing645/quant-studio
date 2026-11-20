"""Check the approved research candidate without enabling it in the application."""

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from app.star50 import Star50Signals, run_star50_backtest
from app.strategies import normalize_strategy_parameters
from research.star50_trend_holding import TrendHoldingSignals
from research.star50_candidates import (
    BandReversionSignals, FastRecoverySignals, Hypothesis, TrendPullbackSignals,
    DEFAULT_ANNUAL_TARGET, absolute_checks, equal_sleeves, simulate,
)


def rising_bars() -> pd.DataFrame:
    """Build a sustained trend with ordinary volume and no daily high breakout."""
    close = 10 + np.arange(110) * .02
    return pd.DataFrame({"datetime": pd.date_range("2024-01-02", periods=len(close), freq="B"),
                         "open": close, "high": close + .2, "low": close - .2,
                         "close": close, "volume": 1000.0})


class TrendHoldingResearchTest(unittest.TestCase):
    """Protect confirmation timing, distinct exit rules and unchanged production dispatch."""

    def setUp(self) -> None:
        """Use the declared risk/cap pair without searching historical parameters."""
        self.params = {**normalize_strategy_parameters("star50_breakout"),
                       "riskBudgetPct": 2, "maxPositionPct": 80}

    def test_candidate_needs_two_recovered_closes_and_not_breakout_volume(self) -> None:
        """A recovery enters on the second completed close, even without a high/volume breakout."""
        bars = rising_bars()
        bars.loc[70, ["open", "high", "low", "close"]] = [10, 10.2, 9.8, 10]
        signals = TrendHoldingSignals(bars, 20, 60, self.params)
        self.assertFalse(signals.entry[70])
        self.assertFalse(signals.entry[71])
        self.assertTrue(signals.entry[72])
        self.assertFalse(Star50Signals(bars, 20, 60, self.params).entry[72])

    def test_slow_average_exit_is_separate_from_fast_average_pullback(self) -> None:
        """Two closes below fast SMA alone must not become the candidate's mean-based exit."""
        bars = rising_bars()
        bars.loc[108:109, ["open", "high", "low", "close"]] = [11.7, 11.9, 11.5, 11.7]
        candidate = TrendHoldingSignals(bars, 20, 60, self.params)
        self.assertTrue(Star50Signals(bars, 20, 60, self.params).exit[109])
        self.assertFalse(candidate.exit[109])
        bars.loc[108:109, ["open", "high", "low", "close"]] = [10, 10.2, 9.8, 10]
        self.assertTrue(TrendHoldingSignals(bars, 20, 60, self.params).exit[109])

    def test_candidate_remains_causal_in_the_shared_execution_ledger(self) -> None:
        """Future shocks cannot change prior opening fills, cash or stop state."""
        original_bars = rising_bars()
        changed_bars = original_bars.copy()
        changed_bars.loc[90:, ["open", "high", "low", "close"]] *= 1.5
        with patch("app.star50.Star50Signals", TrendHoldingSignals):
            original = run_star50_backtest(original_bars, 100000, .0003, 5, 20, 60,
                                           self.params, include_sensitivity=False)
            changed = run_star50_backtest(changed_bars, 100000, .0003, 5, 20, 60,
                                          self.params, include_sensitivity=False)
        self.assertTrue(original["executions"])
        self.assertEqual(original["equityCurve"][:90], changed["equityCurve"][:90])
        self.assertEqual(original["riskManagement"]["riskLineCurve"][:90],
                         changed["riskManagement"]["riskLineCurve"][:90])
        self.assertEqual(original["executions"][0], changed["executions"][0])
        production = run_star50_backtest(original_bars, 100000, .0003, 5, 20, 60,
                                         self.params, include_sensitivity=False)
        self.assertFalse(production["executions"])


def pullback_bars() -> pd.DataFrame:
    """Create a long uptrend and a brief oversold dip that remains above the 200-day mean."""
    close = 10 + np.arange(260) * .01
    close[220:223] = [12, 11.9, 12]
    return pd.DataFrame({"datetime": pd.date_range("2023-01-02", periods=len(close), freq="B"),
                         "open": close.copy(), "high": close + .2, "low": close - .2,
                         "close": close, "volume": 1000.0})


class FixedHypothesesTest(unittest.TestCase):
    """Check new hypotheses and actual cash allocation before trusting their research metrics."""

    def test_selected_annual_target_is_not_confused_with_cumulative_return(self) -> None:
        """High cumulative gains must not pass a 15% annual screen; drawdown remains a separate limit."""
        self.assertEqual(DEFAULT_ANNUAL_TARGET, 15)
        row = {"totalReturnPct": 100, "annualizedReturnPct": 10, "maxDrawdownPct": -15,
               "costSensitivity": [{"totalReturnPct": 110}, {"totalReturnPct": 90}],
               "profitAttribution": {"netPnlLessLargestWinner": 1000}}
        checks = absolute_checks(row, DEFAULT_ANNUAL_TARGET)
        self.assertTrue(checks["positiveReturn"])
        self.assertFalse(checks["annualTargetReached"])
        self.assertTrue(all(absolute_checks({**row, "annualizedReturnPct": 15, "maxDrawdownPct": -20}, 15).values()))
        self.assertFalse(absolute_checks({**row, "annualizedReturnPct": 20, "maxDrawdownPct": -20.01}, 15)["drawdownWithinResearchTarget"])

    def test_pullback_requires_long_warmup_and_exits_on_short_mean_recovery(self) -> None:
        """Ordinary rising prices do not enter; an oversold dip and later recovery use separate bars."""
        bars = pullback_bars()
        params = normalize_strategy_parameters("star50_breakout")
        signals = TrendPullbackSignals(bars, 20, 60, params)
        self.assertFalse(signals.entry[:199].any())
        self.assertFalse(signals.entry[210])
        self.assertTrue(signals.entry[220])
        self.assertFalse(signals.exit[220])
        self.assertTrue(signals.exit[223])
        result = simulate(bars, Hypothesis("pullback", TrendPullbackSignals, "test"))
        self.assertEqual(result["executions"][0]["time"], bars.datetime.iloc[221].isoformat())
        self.assertEqual(result["trades"][0]["exit"], bars.datetime.iloc[224].isoformat())

    def test_same_oversold_drop_below_long_mean_is_rejected(self) -> None:
        """The RSI strategy must not buy a bearish breakdown merely because RSI is low."""
        bars = pullback_bars()
        bars.loc[220, ["open", "high", "low", "close"]] = [9, 9.2, 8.8, 9]
        signals = TrendPullbackSignals(bars, 20, 60, normalize_strategy_parameters("star50_breakout"))
        self.assertFalse(signals.entry[220])

    def test_new_hypotheses_preserve_execution_prefix_when_future_changes(self) -> None:
        """Changes after bar 240 cannot rewrite already executed trades or equity."""
        bars = pullback_bars()
        changed = bars.copy()
        changed.loc[240:, ["open", "high", "low", "close"]] *= 1.7
        for signals in (TrendPullbackSignals, BandReversionSignals, FastRecoverySignals):
            with self.subTest(signals=signals.__name__):
                hypothesis = Hypothesis("causality", signals, "test")
                original = simulate(bars, hypothesis)
                shocked = simulate(changed, hypothesis)
                self.assertEqual(original["equityCurve"][:240], shocked["equityCurve"][:240])
                cutoff = bars.datetime.iloc[240].isoformat()
                self.assertEqual([row for row in original["executions"] if row["time"] < cutoff],
                                 [row for row in shocked["executions"] if row["time"] < cutoff])

    def test_equal_sleeves_use_actual_half_capital_integer_lots_and_shared_total(self) -> None:
        """Two real half-funded simulations must reconcile to one 100,000 account, including entry costs."""
        bars = pullback_bars()
        mixed = equal_sleeves(bars)
        first = simulate(bars, Hypothesis("trend", FastRecoverySignals, "test"), initial=50000)
        second = simulate(bars, Hypothesis("pullback", TrendPullbackSignals, "test"), initial=50000)
        self.assertEqual(mixed["initialCapital"], 100000)
        self.assertAlmostEqual(mixed["finalEquity"], first["finalEquity"] + second["finalEquity"])
        self.assertAlmostEqual(mixed["finalEquity"] - 100000,
                               sum(row["pnl"] for row in mixed["trades"])
                               + sum(row["totalPnl"] for row in mixed["openPositions"]))
        self.assertEqual(mixed["tradeCount"], first["tradeCount"] + second["tradeCount"])
        for leg in (first, second):
            for execution in leg["executions"]:
                self.assertEqual(execution["shares"] % 100, 0)
                self.assertGreaterEqual(execution["cash"], 0)


if __name__ == "__main__":
    unittest.main()
