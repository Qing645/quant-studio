import unittest

import numpy as np
import pandas as pd
import talib
from unittest.mock import patch
from app.engine import _annualizer, _rolling_performance, _strategy_signals, analyze_strategy, run_backtest


class BacktestTimingTest(unittest.TestCase):
    """Check the execution contract for historical strategy signals."""

    def test_removed_strategy_is_rejected_before_reading_market_data(self) -> None:
        """The retired strategy must no longer be executable by the engine."""
        with self.assertRaisesRegex(ValueError, "不支持的策略类型"):
            run_backtest(pd.DataFrame(), 100_000, 0, 0, 20, 60, "dual_ma")

    def test_rejects_invalid_moving_average_order(self) -> None:
        bars = pd.DataFrame({
            "datetime": pd.date_range("2025-01-01", periods=100, freq="D"),
            "open": np.ones(100), "high": np.ones(100),
            "low": np.ones(100), "close": np.ones(100),
            "volume": np.ones(100),
        })
        with self.assertRaisesRegex(ValueError, "快线 < 慢线"):
            run_backtest(bars, 100_000, 0, 0, 20, 20)

    def test_live_strategy_reports_trend_state_for_supported_daily_etf(self) -> None:
        """Expose the selected ETF strategy state using the latest daily close."""
        close = np.linspace(20, 30, 100)
        bars = pd.DataFrame({
            "datetime": pd.date_range("2025-01-01", periods=len(close), freq="D"),
            "open": close, "high": close + 0.2, "low": close - 0.2,
            "close": close, "volume": np.full(len(close), 1000.0),
        })

        result = analyze_strategy(bars, "trend_filter", 20, 60, "159977", "daily")

        self.assertTrue(result["supported"])
        self.assertTrue(result["entryCondition"])
        self.assertFalse(result["exitCondition"])
        self.assertEqual(result["basis"], "日K")

    def test_live_trend_filter_marks_rebound_after_two_bar_exit_confirmation(self) -> None:
        """Separate a countertrend rebound from the confirmed risk exit state."""
        close = np.linspace(10, 30, 240)
        close[-2:] = [20.0, 21.0]
        bars = pd.DataFrame({
            "datetime": pd.date_range("2025-01-01", periods=len(close), freq="D"),
            "open": close, "high": close + 0.2, "low": close - 0.2,
            "close": close, "volume": np.full(len(close), 1000.0),
        })

        result = analyze_strategy(bars, "trend_filter", 20, 60, "159977", "daily")

        self.assertEqual(result["regime"], "rebound_watch")
        self.assertTrue(result["rebound"])
        self.assertTrue(result["exitCondition"])
        self.assertEqual(result["action"], "exit")
        self.assertIn("反弹观察", result["signal"])

    def test_live_strategy_explains_unsupported_symbol_and_timeframe(self) -> None:
        """Return a clear scope state instead of calculating invalid signals."""
        bars = pd.DataFrame()

        wrong_symbol = analyze_strategy(bars, "trend_filter", 20, 60, "510300", "daily")
        wrong_timeframe = analyze_strategy(bars, "trend_filter", 20, 60, "159977", "weekly")

        self.assertFalse(wrong_symbol["supported"])
        self.assertIn("仅支持", wrong_symbol["message"])
        self.assertFalse(wrong_timeframe["supported"])
        self.assertIn("仅支持", wrong_timeframe["message"])

    def test_intraday_strategy_closes_at_complete_session_end(self) -> None:
        """Flatten a minute strategy position only when the supplied session is complete."""
        close = np.linspace(10.0, 10.2, 12)
        bars = pd.DataFrame({
            "datetime": pd.date_range("2026-11-02 09:30", periods=len(close), freq="min"),
            "open": close, "high": close + 0.02, "low": close - 0.02,
            "close": close, "volume": np.full(len(close), 1000.0),
        })

        def signals(_strategy, index, *_args, **_kwargs):
            return (index == 2, False)

        with patch("app.engine._strategy_signals", side_effect=signals):
            result = run_backtest(
                bars, 100_000, 0.0003, 5, 5, 20, "intraday_scalp",
                session_complete=True,
            )

        self.assertFalse(result["openPosition"])
        self.assertEqual(result["tradeCount"], 1)
        self.assertEqual(result["trades"][0]["exit"], bars.iloc[-1]["datetime"].isoformat())
        self.assertIsNone(result["annualizedReturnPct"])
        self.assertIsNone(result["sharpe"])
        self.assertIn("当日分钟样本", result["sampleWarning"])
        self.assertTrue(all(trade["exit"][:10] == trade["entry"][:10] for trade in result["trades"]))

    def test_intraday_partial_sample_keeps_position_open_and_marks_to_market(self) -> None:
        """Do not turn a still-running session sample into a completed trade."""
        close = np.linspace(10.0, 10.2, 12)
        bars = pd.DataFrame({
            "datetime": pd.date_range("2026-11-02 09:30", periods=len(close), freq="min"),
            "open": close, "high": close + 0.02, "low": close - 0.02,
            "close": close, "volume": np.full(len(close), 1000.0),
        })

        def signals(_strategy, index, *_args, **_kwargs):
            return (index == 2, False)

        with patch("app.engine._strategy_signals", side_effect=signals):
            result = run_backtest(bars, 100_000, 0, 0, 5, 20, "intraday_scalp")

        self.assertTrue(result["openPosition"])
        self.assertEqual(result["tradeCount"], 0)
        self.assertGreater(result["finalEquity"], 100_000)
        self.assertEqual(result["openPositions"][0]["currentPrice"], float(close[-1]))

    def test_intraday_backtest_flattens_each_completed_day_in_a_multi_day_sample(self) -> None:
        """Close completed sessions at their last bar while preserving the final partial session."""
        close = np.linspace(10.0, 10.2, 8)
        dates = pd.DatetimeIndex([
            "2026-11-02 09:30", "2026-11-02 09:31", "2026-11-02 09:32", "2026-11-02 15:00",
            "2026-11-03 09:30", "2026-11-03 09:31", "2026-11-03 09:32", "2026-11-03 09:33",
        ])
        bars = pd.DataFrame({
            "datetime": dates, "open": close, "high": close + 0.02,
            "low": close - 0.02, "close": close, "volume": np.full(len(close), 1000.0),
        })

        def signals(_strategy, index, *_args, **_kwargs):
            return (index in {2, 6}, False)

        with patch("app.engine._strategy_signals", side_effect=signals):
            result = run_backtest(bars, 100_000, 0, 0, 5, 20, "intraday_scalp")

        self.assertTrue(result["openPosition"])
        self.assertEqual(result["tradeCount"], 1)
        self.assertEqual(result["trades"][0]["exit"], dates[3].isoformat())
        self.assertEqual(result["openPositions"][0]["entry"], dates[6].isoformat())

    def test_swing_breakout_reports_comparable_benchmark_rolling_and_cost_scenarios(self) -> None:
        """Compare the daily swing rule with its tradable sample and cost assumptions."""
        close = 10 + np.arange(420) * 0.02 + np.sin(np.arange(420) / 5) * 0.2
        volume = np.full(len(close), 1000.0)
        volume[80::40] = 2000.0
        bars = pd.DataFrame({
            "datetime": pd.date_range("2024-01-01", periods=len(close), freq="D"),
            "open": close, "high": close + 0.15, "low": close - 0.15,
            "close": close, "volume": volume,
        })

        result = run_backtest(bars, 100_000, 0.0003, 5, 10, 20, "swing_breakout")

        self.assertEqual(result["benchmark"]["name"], "买入持有")
        self.assertEqual(len(result["benchmark"]["equityCurve"]), len(bars))
        self.assertTrue(result["rollingPerformance"])
        self.assertEqual(
            [scenario["scenario"] for scenario in result["costSensitivity"]],
            ["零成本", "双倍成本"],
        )

    def test_swing_breakout_keeps_price_above_sma60_as_entry_filter(self) -> None:
        """Retain the established SMA60 level filter without an unstable slope gate."""
        current = 70
        close = np.full(80, 10.0)
        high = np.full(80, 10.5)
        volume = np.full(80, 1000.0)
        close[current] = 11.0
        high[current] = 11.1
        volume[current] = 1300.0
        fast = np.full(80, 10.0)
        slow = np.full(80, 10.0)
        rising_sma = np.linspace(9.0, 10.0, 80)
        falling_sma = rising_sma.copy()
        falling_sma[current] = falling_sma[current - 5] - 0.01
        atr = np.full(80, 0.2)

        rising_entry, _ = _strategy_signals(
            "swing_breakout", current + 1, close, fast, slow, falling_sma, {},
            high=high, volume=volume, atr=atr, slow_period=20,
        )

        self.assertTrue(rising_entry)

    def test_swing_breakout_confirms_fast_line_exit_for_two_closes(self) -> None:
        """Avoid exiting on one noisy close while preserving the ATR risk exit."""
        current = 70
        close = np.full(80, 10.0)
        high = np.full(80, 10.2)
        volume = np.full(80, 1000.0)
        fast = np.full(80, 10.0)
        slow = np.full(80, 10.0)
        rising_sma = np.linspace(9.0, 10.0, 80)
        atr = np.full(80, 0.2)
        close[current] = 9.8
        fast[current] = 10.0

        _, one_bar_exit = _strategy_signals(
            "swing_breakout", current + 1, close, fast, slow, rising_sma, {},
            high=high, volume=volume, atr=atr, slow_period=20,
        )
        close[current - 1] = 9.8
        fast[current - 1] = 10.0
        _, confirmed_exit = _strategy_signals(
            "swing_breakout", current + 1, close, fast, slow, rising_sma, {},
            high=high, volume=volume, atr=atr, slow_period=20,
        )

        self.assertFalse(one_bar_exit)
        self.assertTrue(confirmed_exit)

    def test_intraday_costs_are_charged_for_both_open_and_close(self) -> None:
        """Charge round-trip costs to the bars where the trades actually execute."""
        close = np.concatenate([
            np.full(30, 10.0),
            np.linspace(10, 10.3, 20),
            np.linspace(10.3, 9.8, 20),
        ])
        bars = pd.DataFrame({
            "datetime": pd.date_range("2026-11-02 09:30", periods=len(close), freq="min"),
            "open": close, "high": close + 0.02, "low": close - 0.02,
            "close": close, "volume": np.full(len(close), 1000.0),
        })

        result = run_backtest(bars, 100_000, 0.0003, 5, 3, 8, "intraday_scalp")
        no_cost = run_backtest(bars, 100_000, 0, 0, 3, 8, "intraday_scalp")

        self.assertFalse(result["openPosition"])
        self.assertGreater(result["tradeCount"], 0)
        self.assertLess(result["finalEquity"], no_cost["finalEquity"])

    def test_intraday_exit_signal_waits_until_minimum_holding_period(self) -> None:
        """Ignore indicator exits for three bars while retaining the forced close."""
        close = np.linspace(10, 10.1, 8)
        bars = pd.DataFrame({
            "datetime": pd.date_range("2026-11-02 09:30", periods=len(close), freq="min"),
            "open": close, "high": close + 0.02, "low": close - 0.02,
            "close": close, "volume": np.full(len(close), 1000.0),
        })

        def signals(_strategy, index, *_args, **_kwargs):
            return (index == 2, index in {3, 4, 5})

        with patch("app.engine._strategy_signals", side_effect=signals):
            result = run_backtest(bars, 100_000, 0, 0, 3, 8, "intraday_scalp")

        self.assertEqual(result["tradeCount"], 1)
        self.assertEqual(result["trades"][0]["entry"], bars.iloc[2]["datetime"].isoformat())
        self.assertEqual(result["trades"][0]["exit"], bars.iloc[5]["datetime"].isoformat())
        self.assertEqual(result["benchmark"]["name"], "当日买入持有")
        self.assertAlmostEqual(
            result["benchmark"]["totalReturnPct"],
            (close[-1] / close[0] - 1) * 100,
        )

    def test_live_intraday_exit_is_labeled_as_a_condition_not_an_action(self) -> None:
        """Avoid presenting indicator conditions as position-aware sell instructions."""
        close = np.full(30, 10.0)
        bars = pd.DataFrame({
            "datetime": pd.date_range("2026-11-02 09:30", periods=len(close), freq="min"),
            "open": close, "high": close + 0.02, "low": close - 0.02,
            "close": close, "volume": np.full(len(close), 1000.0),
        })

        with patch("app.engine._session_vwap", return_value=np.full(len(close), 11.0)):
            result = analyze_strategy(bars, "intraday_scalp", 5, 20, "159977", "intraday")

        self.assertTrue(result["exitCondition"])
        self.assertEqual(result["signal"], "日内离场观察条件触发")
        self.assertEqual(result["action"], "observe")
        self.assertIn("没有实时持仓与持有时长", result["message"])

    def test_swing_breakout_live_signal_requires_daily_scope(self) -> None:
        """Keep the daily swing rule distinct from minute analysis."""
        result = analyze_strategy(pd.DataFrame(), "swing_breakout", 10, 20, "159977", "intraday")

        self.assertFalse(result["supported"])
        self.assertIn("日K", result["message"])

    def test_trend_filter_enters_above_long_sma_and_exits_below_it(self) -> None:
        """Execute trend state changes on the following bar open."""
        close = np.concatenate([np.linspace(20, 15, 120), np.linspace(15, 30, 160), np.linspace(30, 14, 180)])
        bars = pd.DataFrame({
            "datetime": pd.date_range("2024-01-01", periods=len(close), freq="D"),
            "open": close + 0.1,
            "high": close + 0.3,
            "low": close - 0.3,
            "close": close,
            "volume": np.full(len(close), 1000.0),
        })
        slow = talib.SMA(close.astype(float), timeperiod=120)
        regime = talib.SMA(close.astype(float), timeperiod=200)
        confirmed_exit_indexes = [
            index for index in range(1, len(close))
            if np.isfinite([
                close[index], close[index - 1], slow[index], slow[index - 1],
                regime[index], regime[index - 1],
            ]).all()
            and (
                (close[index] <= slow[index] and close[index - 1] <= slow[index - 1])
                or (close[index] <= regime[index] and close[index - 1] <= regime[index - 1])
            )
        ]

        result = run_backtest(bars, 100_000, 0, 0, 20, 120, "trend_filter")

        self.assertTrue(result["trades"])
        self.assertTrue(confirmed_exit_indexes)
        self.assertEqual(result["trades"][-1]["exit"], bars.iloc[confirmed_exit_indexes[0] + 1]["datetime"].isoformat())
        self.assertIn("benchmark", result)
        self.assertGreaterEqual(result["exposurePct"], 0)
        self.assertLessEqual(result["exposurePct"], 100)

    def test_chinext_trend_filter_scales_entry_size_by_prior_realized_volatility(self) -> None:
        """Size entries from completed bars and preserve unused capital as cash."""
        rng = np.random.default_rng(101)
        close = np.cumprod(1 + rng.normal(0.0008, 0.025, 500))
        open_price = close * (1 + rng.normal(0, 0.001, len(close)))
        bars = pd.DataFrame({
            "datetime": pd.date_range("2024-01-01", periods=len(close), freq="D"),
            "open": open_price,
            "high": np.maximum(open_price, close) * 1.01,
            "low": np.minimum(open_price, close) * 0.99,
            "close": close,
            "volume": np.full(len(close), 1000.0),
        })

        result = run_backtest(bars, 100_000, 0.0003, 5, 20, 60, "trend_filter")

        self.assertTrue(result["openPositions"])
        self.assertLess(result["openPositions"][0]["entryCapital"], 100_000)
        self.assertLess(result["exposurePct"], 100)
        self.assertEqual(result["positionSizingTargetVolatilityPct"], 20.0)
        self.assertGreater(result["rebalanceCount"], 0)
        self.assertAlmostEqual(
            result["finalEquity"] - result["initialCapital"],
            sum(trade["pnl"] for trade in result["trades"])
            + result["openPositions"][0]["totalPnl"],
            places=6,
        )

    def test_trend_filter_sharpe_includes_first_eligible_holding_day(self) -> None:
        """Include the return ending on the first bar where SMA60 can execute."""
        close = np.ones(100, dtype=float)
        close[60] = 1.1
        close[61:] = 1.2 + np.linspace(0, 0.2, len(close) - 61)
        open_price = close.copy()
        open_price[61] = 1.1
        bars = pd.DataFrame({
            "datetime": pd.date_range("2025-01-01", periods=len(close), freq="D"),
            "open": open_price,
            "high": np.maximum(open_price, close) * 1.01,
            "low": np.minimum(open_price, close) * 0.99,
            "close": close,
            "volume": np.full(len(close), 1000.0),
        })

        result = run_backtest(bars, 100_000, 0, 0, 20, 60, "trend_filter")

        curve = np.array([point["value"] for point in result["equityCurve"]])
        returns = np.diff(curve) / curve[:-1]
        comparison_start = 61
        expected_returns = returns[comparison_start - 1:]
        expected_sharpe = np.sqrt(252) * expected_returns.mean() / expected_returns.std()
        self.assertAlmostEqual(result["sharpe"], expected_sharpe)

    def test_trend_filter_annualizes_over_the_full_equity_curve(self) -> None:
        """Count cash warmup bars because initial capital is present from bar one."""
        close = np.linspace(20, 25, 100)
        bars = pd.DataFrame({
            "datetime": pd.date_range("2025-01-01", periods=len(close), freq="D"),
            "open": close, "high": close + 0.2, "low": close - 0.2,
            "close": close, "volume": np.full(100, 1000.0),
        })

        result = run_backtest(bars, 100_000, 0, 0, 20, 60, "trend_filter")

        expected = (
            (result["finalEquity"] / result["initialCapital"])
            ** (252 / (len(bars) - 1)) - 1
        ) * 100
        self.assertAlmostEqual(result["annualizedReturnPct"], expected)

    def test_annualizer_matches_minute_daily_weekly_and_monthly_bars(self) -> None:
        self.assertEqual(_annualizer(1), 252 * 240)
        self.assertEqual(_annualizer(24 * 60), 252)
        self.assertEqual(_annualizer(7 * 24 * 60), 52)
        self.assertEqual(_annualizer(30 * 24 * 60), 12)

    def test_rolling_performance_reports_returns_drawdown_exposure_and_costs(self) -> None:
        """Keep each rolling metric tied to the bars and costs inside its window."""
        dates = [f"2025-01-0{index}T00:00:00" for index in range(1, 9)]
        result = _rolling_performance(
            dates,
            np.array([100, 110, 100, 105, 105, 115, 110, 120], dtype=float),
            np.array([100, 105, 102, 108, 110, 112, 115, 118], dtype=float),
            np.array([0, 50, 50, 100, 100, 100, 50, 0], dtype=float),
            np.array([0, 2, 0, 3, 0, 1, 0, 0], dtype=float),
            dates[0],
            dates[-1],
        )

        self.assertEqual(len(result), 4)
        self.assertIn("benchmarkReturnPct", result[0])
        self.assertGreaterEqual(result[0]["maxDrawdownPct"], -100)
        self.assertGreaterEqual(result[0]["exposurePct"], 0)
        self.assertEqual(sum(period["transactionCost"] for period in result), 6)

    def test_backtest_rejects_invalid_ohlcv_rows(self) -> None:
        bars = pd.DataFrame({
            "datetime": pd.date_range("2025-01-01", periods=3, freq="D"),
            "open": [10.0, 10.0, 10.0], "high": [10.2, 9.8, 10.2],
            "low": [9.8, 9.9, 9.8], "close": [10.0, 10.0, 10.0],
            "volume": [1000.0, 1000.0, 1000.0],
        })
        with self.assertRaisesRegex(ValueError, "最高价和最低价"):
            run_backtest(bars, 100_000, 0, 0, 2, 3, "trend_filter")


if __name__ == "__main__":
    unittest.main()
