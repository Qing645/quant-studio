from datetime import datetime, timedelta
import unittest

import numpy as np
import pandas as pd
import talib

from app.engine import _annualizer, _rolling_performance, analyze_strategy, run_backtest, run_ml_portfolio
from app.ml import _features, walk_forward_probabilities


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

    def test_live_ml_strategy_reports_insufficient_training_history(self) -> None:
        """Report training limitations as signal state rather than failing the market request."""
        close = np.linspace(20, 30, 120)
        bars = pd.DataFrame({
            "datetime": pd.date_range("2025-01-01", periods=len(close), freq="D"),
            "open": close, "high": close + 0.2, "low": close - 0.2,
            "close": close, "volume": np.full(len(close), 1000.0),
        })

        result = analyze_strategy(bars, "ml_direction", 20, 60, "159977", "daily")

        self.assertTrue(result["supported"])
        self.assertEqual(result["signal"], "数据不足")
        self.assertTrue(result["message"])

    def test_growth_momentum_entry_executes_on_the_following_bar_open(self) -> None:
        close = 20 + np.arange(400) * 0.015 + 0.5 * np.sin(np.arange(400) / 7)
        bars = pd.DataFrame({
            "datetime": [datetime(2025, 1, 1) + timedelta(days=i) for i in range(len(close))],
            "open": close + 0.1,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "volume": np.full(len(close), 1000.0),
        })
        fast = talib.SMA(close.astype(float), timeperiod=20)
        slow = talib.SMA(close.astype(float), timeperiod=60)
        histogram = talib.MACD(close.astype(float), fastperiod=12, slowperiod=26, signalperiod=9)[2]
        rsi = talib.RSI(close.astype(float), timeperiod=14)
        entry_indexes = [
            index for index in range(1, len(close))
            if histogram[index - 1] <= 0 < histogram[index]
            and fast[index] > slow[index]
            and close[index] > slow[index]
            and 50 <= rsi[index] <= 75
        ]

        result = run_backtest(bars, 100_000, 0, 0, 20, 60, "growth_momentum")

        self.assertTrue(entry_indexes)
        self.assertTrue(result["trades"])
        self.assertEqual(result["trades"][0]["entry"], bars.iloc[entry_indexes[0] + 1]["datetime"].isoformat())
        self.assertEqual(result["strategyType"], "growth_momentum")

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
        bars = pd.DataFrame({
            "datetime": pd.date_range("2024-01-01", periods=len(close), freq="D"),
            "open": close * (1 + rng.normal(0, 0.001, len(close))),
            "high": close * 1.01,
            "low": close * 0.99,
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
            "high": close * 1.01,
            "low": close * 0.99,
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

    def test_ml_strategy_reports_held_out_metrics_and_backtest_curve(self) -> None:
        rng = np.random.default_rng(17)
        close = 30 + np.cumsum(rng.normal(0, 0.45, 320))
        bars = pd.DataFrame({
            "datetime": pd.date_range("2024-01-01", periods=len(close), freq="D"),
            "open": close + rng.normal(0, 0.08, len(close)),
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "volume": rng.integers(1000, 5000, len(close)),
        })

        result = run_backtest(bars, 100_000, 0.0003, 5, 20, 60, "ml_direction")

        self.assertEqual(len(result["equityCurve"]), len(bars))
        self.assertEqual(result["strategyType"], "ml_direction")
        self.assertGreater(result["machineLearning"]["outOfSampleSamples"], 0)
        self.assertGreaterEqual(result["machineLearning"]["brierScore"], 0)
        self.assertLessEqual(result["machineLearning"]["brierScore"], 1)

    def test_ml_walk_forward_does_not_use_bars_after_prediction_window(self) -> None:
        rng = np.random.default_rng(23)
        close = 30 + np.cumsum(rng.normal(0, 0.4, 280))
        bars = pd.DataFrame({
            "datetime": pd.date_range("2024-01-01", periods=len(close), freq="D"),
            "open": close, "high": close + 0.2, "low": close - 0.2,
            "close": close, "volume": rng.integers(1000, 4000, len(close)),
        })
        altered = bars.copy()
        altered.loc[230:, ["open", "high", "low", "close"]] *= 1.4

        original_probabilities, _ = walk_forward_probabilities(bars)
        altered_probabilities, _ = walk_forward_probabilities(altered)

        np.testing.assert_allclose(original_probabilities[:230], altered_probabilities[:230], equal_nan=True)

    def test_ml_strategy_rejects_insufficient_daily_history(self) -> None:
        close = np.linspace(20, 30, 120)
        bars = pd.DataFrame({
            "datetime": pd.date_range("2025-01-01", periods=len(close), freq="D"),
            "open": close, "high": close + 0.2, "low": close - 0.2,
            "close": close, "volume": np.full(len(close), 1000.0),
        })

        with self.assertRaisesRegex(ValueError, "样本不足"):
            run_backtest(bars, 100_000, 0, 0, 20, 60, "ml_direction")

    def test_ml_features_never_emit_infinite_ratios_for_zero_prices(self) -> None:
        close = 25 + np.sin(np.arange(220) / 8)
        close[91] = 0
        bars = pd.DataFrame({
            "datetime": pd.date_range("2024-01-01", periods=len(close), freq="D"),
            "open": close, "high": close + 0.2, "low": np.maximum(close - 0.2, 0),
            "close": close, "volume": np.full(len(close), 1000.0),
        })

        features, _ = _features(bars)

        self.assertFalse(np.isinf(features).any())
        self.assertTrue(np.isnan(features).any())

    def test_ml_portfolio_allocates_equal_capital_and_builds_buy_hold_benchmark(self) -> None:
        rng = np.random.default_rng(31)
        bars_by_symbol = {}
        for symbol, offset in (("159977", 0.0), ("510300", 0.03)):
            close = 20 + offset + np.cumsum(rng.normal(0, 0.3, 300))
            bars_by_symbol[symbol] = pd.DataFrame({
                "datetime": pd.date_range("2024-01-01", periods=len(close), freq="D"),
                "open": close, "high": close + 0.2, "low": close - 0.2,
                "close": close, "volume": rng.integers(1000, 4000, len(close)),
            })

        result = run_ml_portfolio(bars_by_symbol, 100_000, 0.0003, 5, 20, 60)

        self.assertEqual(result["symbols"], ["159977", "510300"])
        self.assertEqual([item["initialCapital"] for item in result["symbolResults"]], [50_000, 50_000])
        self.assertEqual(len(result["equityCurve"]), 300)
        self.assertIn("totalReturnPct", result["benchmark"])
        self.assertEqual(result["excludedSymbols"], [])
        self.assertEqual(len(result["rollingPerformance"]), 4)
        self.assertTrue(all("transactionCost" in period for period in result["rollingPerformance"]))
        self.assertTrue(all("symbol" in trade for trade in result["trades"]))
        self.assertTrue(all("entryPrice" in trade and "exitPrice" in trade for trade in result["trades"]))

    def test_ml_portfolio_excludes_symbols_without_training_history(self) -> None:
        rng = np.random.default_rng(33)
        close = 20 + np.cumsum(rng.normal(0, 0.3, 300))
        valid = pd.DataFrame({
            "datetime": pd.date_range("2024-01-01", periods=len(close), freq="D"),
            "open": close, "high": close + 0.2, "low": close - 0.2,
            "close": close, "volume": rng.integers(1000, 4000, len(close)),
        })
        short = valid.iloc[:120].copy()

        result = run_ml_portfolio({"159977": valid, "000001": short}, 100_000, 0, 0, 20, 60)

        self.assertEqual(result["symbols"], ["159977"])
        self.assertEqual(result["excludedSymbols"][0]["symbol"], "000001")
        self.assertIn("有效日线样本不足", result["excludedSymbols"][0]["reason"])
        self.assertEqual(result["symbolResults"][0]["initialCapital"], 100_000)


if __name__ == "__main__":
    unittest.main()
