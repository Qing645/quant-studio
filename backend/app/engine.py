"""Reproducible, long-only research backtest for editable signal rules."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import talib


def run_backtest(
    bars: pd.DataFrame,
    initial_capital: float,
    fee_rate: float,
    slippage_bps: float,
    fast_period: int,
    slow_period: int,
) -> dict[str, Any]:
    """Simulate next-bar execution and return portfolio and trade statistics."""
    if fast_period < 2 or slow_period <= fast_period or slow_period > 120:
        raise ValueError("均线周期需满足 2 <= 快线 < 慢线 <= 120")
    if initial_capital <= 0 or not 0 <= fee_rate <= 0.02 or not 0 <= slippage_bps <= 500:
        raise ValueError("资金、手续费或滑点参数超出允许范围")
    close = bars["close"].to_numpy(dtype=np.float64)
    open_price = bars["open"].to_numpy(dtype=np.float64)
    fast = talib.SMA(close, timeperiod=fast_period)
    slow = talib.SMA(close, timeperiod=slow_period)
    cash = initial_capital
    shares = 0.0
    entry_value = 0.0
    entry_time = ""
    equity = []
    trades = []
    cost_rate = fee_rate + slippage_bps / 10_000

    # A signal on bar N executes at bar N+1 open to avoid same-bar lookahead.
    for index in range(len(bars)):
        if index > 1 and np.isfinite([fast[index - 2], slow[index - 2], fast[index - 1], slow[index - 1]]).all():
            crossed_up = fast[index - 2] <= slow[index - 2] and fast[index - 1] > slow[index - 1]
            crossed_down = fast[index - 2] >= slow[index - 2] and fast[index - 1] < slow[index - 1]
            execution_price = open_price[index] * (1 + cost_rate)
            if shares == 0 and crossed_up:
                shares = cash / execution_price
                entry_value = cash
                entry_time = bars.iloc[index]["datetime"].isoformat()
                cash = 0.0
            elif shares > 0 and crossed_down:
                exit_price = open_price[index] * (1 - cost_rate)
                proceeds = shares * exit_price
                trades.append({
                    "entry": entry_time,
                    "exit": bars.iloc[index]["datetime"].isoformat(),
                    "returnPct": (proceeds / entry_value - 1) * 100,
                    "pnl": proceeds - entry_value,
                })
                cash = proceeds
                shares = 0.0
                entry_value = 0.0
        equity_value = cash + shares * close[index]
        equity.append({"time": bars.iloc[index]["datetime"].isoformat(), "value": equity_value})

    final_equity = equity[-1]["value"]
    curve = np.array([point["value"] for point in equity], dtype=float)
    peaks = np.maximum.accumulate(curve)
    drawdowns = (curve - peaks) / peaks
    returns = np.diff(curve) / curve[:-1] if len(curve) > 1 else np.array([])
    interval_minutes = bars["datetime"].diff().dt.total_seconds().median() / 60
    annualizer = _annualizer(interval_minutes)
    annualized = (final_equity / initial_capital) ** (annualizer / max(len(bars), 1)) - 1
    return {
        "initialCapital": initial_capital,
        "finalEquity": float(final_equity),
        "totalReturnPct": (final_equity / initial_capital - 1) * 100,
        "annualizedReturnPct": annualized * 100,
        "maxDrawdownPct": float(drawdowns.min() * 100),
        "sharpe": float(np.sqrt(annualizer) * returns.mean() / returns.std()) if returns.size and returns.std() else 0.0,
        "winRatePct": float(np.mean([trade["returnPct"] > 0 for trade in trades]) * 100) if trades else 0.0,
        "tradeCount": len(trades),
        "openPosition": shares > 0,
        "equityCurve": equity,
        "trades": trades,
        "caveat": "研究版均线交叉回测；数据区间受公开源限制，未建模涨跌停、分红税和市场冲击。",
    }


def _annualizer(interval_minutes: float) -> int:
    """Choose annual periods from the observed bar spacing for minute through month bars."""
    if interval_minutes <= 1.5:
        return 252 * 240
    if interval_minutes <= 3 * 24 * 60:
        return 252
    if interval_minutes <= 10 * 24 * 60:
        return 52
    return 12
