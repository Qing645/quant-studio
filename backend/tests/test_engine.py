from datetime import datetime, timedelta
import unittest

import numpy as np
import pandas as pd
import talib

from app.engine import _annualizer, run_backtest


class BacktestTimingTest(unittest.TestCase):
    """Check the execution contract for historical strategy signals."""

    def test_cross_signal_executes_on_the_following_bar_open(self) -> None:
        close = 10 + np.sin(np.arange(160) / 4)
        bars = pd.DataFrame({
            "datetime": [datetime(2025, 1, 1) + timedelta(days=i) for i in range(len(close))],
            "open": close + 0.25,
            "high": close + 0.3,
            "low": close - 0.3,
            "close": close,
            "volume": np.full(len(close), 1000.0),
        })
        fast = talib.SMA(close.astype(float), timeperiod=3)
        slow = talib.SMA(close.astype(float), timeperiod=8)
        cross_indexes = [
            index for index in range(1, len(close))
            if np.isfinite([fast[index - 1], slow[index - 1], fast[index], slow[index]]).all()
            and fast[index - 1] <= slow[index - 1]
            and fast[index] > slow[index]
        ]

        result = run_backtest(bars, 100_000, 0, 0, 3, 8)

        self.assertTrue(cross_indexes)
        self.assertTrue(result["trades"])
        self.assertEqual(result["trades"][0]["entry"], bars.iloc[cross_indexes[0] + 1]["datetime"].isoformat())

    def test_rejects_invalid_moving_average_order(self) -> None:
        bars = pd.DataFrame({
            "datetime": pd.date_range("2025-01-01", periods=100, freq="D"),
            "open": np.ones(100), "high": np.ones(100),
            "low": np.ones(100), "close": np.ones(100),
        })
        with self.assertRaisesRegex(ValueError, "快线 < 慢线"):
            run_backtest(bars, 100_000, 0, 0, 20, 20)

    def test_annualizer_matches_minute_daily_weekly_and_monthly_bars(self) -> None:
        self.assertEqual(_annualizer(1), 252 * 240)
        self.assertEqual(_annualizer(24 * 60), 252)
        self.assertEqual(_annualizer(7 * 24 * 60), 52)
        self.assertEqual(_annualizer(30 * 24 * 60), 12)


if __name__ == "__main__":
    unittest.main()
