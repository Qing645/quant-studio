"""Reproducible, long-only research backtest for editable signal rules."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import talib

from .strategies import get_strategy_template

SUPPORTED_STRATEGIES = {"trend_filter", "swing_breakout", "intraday_scalp"}
REQUIRED_BAR_COLUMNS = ("datetime", "open", "high", "low", "close", "volume")


def _validate_bars(bars: pd.DataFrame) -> None:
    """Reject malformed OHLCV input before indicators or equity calculations run."""
    missing = [column for column in REQUIRED_BAR_COLUMNS if column not in bars.columns]
    if missing:
        raise ValueError(f"K线数据缺少字段：{', '.join(missing)}")
    if len(bars) < 2:
        raise ValueError("至少需要两根K线才能回测")
    numeric = bars[["open", "high", "low", "close", "volume"]].to_numpy(dtype=float)
    timestamps = pd.to_datetime(bars["datetime"], errors="coerce")
    if not np.isfinite(numeric).all() or timestamps.isna().any():
        raise ValueError("K线数据包含空值或非有限数值")
    if np.any(numeric[:, :4] <= 0) or np.any(numeric[:, 4] < 0):
        raise ValueError("K线价格必须为正数，成交量不能为负数")
    if np.any(numeric[:, 1] < np.maximum(numeric[:, 0], numeric[:, 3])) or np.any(numeric[:, 2] > np.minimum(numeric[:, 0], numeric[:, 3])):
        raise ValueError("K线最高价和最低价与开盘价、收盘价不一致")
    if not timestamps.is_monotonic_increasing or timestamps.duplicated().any():
        raise ValueError("K线时间必须严格递增且不能重复")

def run_backtest(
    bars: pd.DataFrame,
    initial_capital: float,
    fee_rate: float,
    slippage_bps: float,
    fast_period: int,
    slow_period: int,
    strategy_type: str = "trend_filter",
) -> dict[str, Any]:
    """Simulate next-bar execution and return portfolio and trade statistics."""
    if fast_period < 2 or slow_period <= fast_period or slow_period > 250:
        raise ValueError("均线周期需满足 2 <= 快线 < 慢线 <= 250")
    if strategy_type not in SUPPORTED_STRATEGIES:
        raise ValueError("不支持的策略类型")
    _validate_bars(bars)
    if initial_capital <= 0 or not 0 <= fee_rate <= 0.02 or not 0 <= slippage_bps <= 500:
        raise ValueError("资金、手续费或滑点参数超出允许范围")
    close = bars["close"].to_numpy(dtype=np.float64)
    open_price = bars["open"].to_numpy(dtype=np.float64)
    high = bars["high"].to_numpy(dtype=np.float64)
    low = bars["low"].to_numpy(dtype=np.float64)
    fast = talib.SMA(close, timeperiod=fast_period)
    slow = talib.SMA(close, timeperiod=slow_period)
    regime_sma = talib.SMA(close, timeperiod=60) if strategy_type == "swing_breakout" else slow
    ema_fast = talib.EMA(close, timeperiod=fast_period)
    ema_slow = talib.EMA(close, timeperiod=slow_period)
    atr = talib.ATR(high, low, close, timeperiod=20)
    volume = bars["volume"].to_numpy(dtype=np.float64)
    vwap = _session_vwap(bars) if strategy_type == "intraday_scalp" else np.full(len(bars), np.nan)
    cash = initial_capital
    shares = 0.0
    entry_value = 0.0
    entry_time = ""
    entry_price = 0.0
    entry_cost = 0.0
    realized_proceeds = 0.0
    equity = []
    trades = []
    exposure_curve = []
    transaction_cost_curve = []
    rebalance_count = 0
    cost_rate = fee_rate + slippage_bps / 10_000
    template = get_strategy_template(strategy_type)
    annualized_volatility = np.full(len(bars), np.nan, dtype=float)
    if strategy_type == "trend_filter":
        # Use completed returns only so sizing cannot see the execution bar.
        daily_returns = pd.Series(np.diff(np.log(close), prepend=np.nan))
        annualized_volatility = daily_returns.rolling(20, min_periods=20).std().to_numpy() * np.sqrt(252)
    # A signal on bar N executes at bar N+1 open to avoid same-bar lookahead.
    for index in range(len(bars)):
        if index > 1:
            crossed_up, crossed_down = _strategy_signals(
                strategy_type, index, close, fast, slow, regime_sma, template,
                high=high, volume=volume,
                ema_fast=ema_fast, ema_slow=ema_slow, atr=atr, vwap=vwap,
                fast_period=fast_period, slow_period=slow_period,
            )
            force_intraday_close = strategy_type == "intraday_scalp" and index == len(bars) - 1 and shares > 0
            execution_price = open_price[index] * (1 + cost_rate)
            if shares == 0 and crossed_up and not (strategy_type == "intraday_scalp" and index == len(bars) - 1):
                if strategy_type == "trend_filter":
                    signal_volatility = annualized_volatility[index - 1]
                    # Keep excess capital in cash when volatility exceeds the risk budget.
                    target_weight = min(1.0, 0.20 / signal_volatility) if np.isfinite(signal_volatility) and signal_volatility > 0 else 0.0
                    entry_value = cash * target_weight
                else:
                    entry_value = cash
                shares = entry_value / execution_price
                entry_time = bars.iloc[index]["datetime"].isoformat()
                entry_price = float(execution_price)
                entry_cost = float(shares * open_price[index] * cost_rate)
                transaction_cost_curve.append(entry_cost)
                cash -= entry_value
            elif shares > 0 and (crossed_down or force_intraday_close):
                raw_exit_price = close[index] if force_intraday_close else open_price[index]
                exit_price = raw_exit_price * (1 - cost_rate)
                proceeds = realized_proceeds + shares * exit_price
                exit_cost = float(shares * raw_exit_price * cost_rate)
                total_pnl = proceeds - entry_value
                trades.append({
                    "entry": entry_time,
                    "exit": bars.iloc[index]["datetime"].isoformat(),
                    "entryPrice": entry_price,
                    "exitPrice": float(exit_price),
                    "returnPct": float(total_pnl / entry_value * 100) if entry_value else 0.0,
                    "pnl": float(total_pnl),
                    "transactionCost": float(entry_cost + exit_cost),
                })
                transaction_cost_curve.append(exit_cost)
                cash += shares * exit_price
                shares = 0.0
                entry_value = 0.0
                entry_price = 0.0
                entry_cost = 0.0
                realized_proceeds = 0.0
            elif (
                strategy_type == "trend_filter"
                and shares > 0
                and index > 0
                and bars.iloc[index]["datetime"].month != bars.iloc[index - 1]["datetime"].month
            ):
                signal_volatility = annualized_volatility[index - 1]
                if np.isfinite(signal_volatility) and signal_volatility > 0:
                    target_weight = min(1.0, 0.20 / signal_volatility)
                    current_value = shares * open_price[index]
                    target_value = (cash + current_value) * target_weight
                    delta_value = target_value - current_value
                    if delta_value > 1e-8:
                        buy_shares = delta_value / execution_price
                        cash -= delta_value
                        shares += buy_shares
                        entry_value += delta_value
                        adjustment_cost = float(buy_shares * open_price[index] * cost_rate)
                        entry_cost += adjustment_cost
                        transaction_cost_curve.append(adjustment_cost)
                        rebalance_count += 1
                    elif delta_value < -1e-8:
                        sell_shares = min(shares, -delta_value / open_price[index])
                        sell_price = open_price[index] * (1 - cost_rate)
                        sell_proceeds = sell_shares * sell_price
                        adjustment_cost = float(sell_shares * open_price[index] * cost_rate)
                        cash += sell_proceeds
                        shares -= sell_shares
                        realized_proceeds += sell_proceeds
                        entry_cost += adjustment_cost
                        transaction_cost_curve.append(adjustment_cost)
                        rebalance_count += 1
        equity_value = cash + shares * close[index]
        equity.append({"time": bars.iloc[index]["datetime"].isoformat(), "value": float(equity_value)})
        exposure_curve.append(
            float(shares * close[index] / equity_value)
            if strategy_type == "trend_filter" and equity_value else float(shares > 0)
        )
        if len(transaction_cost_curve) < index + 1:
            transaction_cost_curve.append(0.0)

    final_equity = equity[-1]["value"]
    curve = np.array([point["value"] for point in equity], dtype=float)
    peaks = np.maximum.accumulate(curve)
    drawdowns = (curve - peaks) / peaks
    returns = np.diff(curve) / curve[:-1] if len(curve) > 1 else np.array([])
    interval_minutes = bars["datetime"].diff().dt.total_seconds().median() / 60
    annualizer = _annualizer(interval_minutes)
    comparison_start = min(len(bars) - 1, max(slow_period, 20) + 1) if strategy_type == "trend_filter" else 0
    # Equity includes the initial cash balance during warmup, so annualize over
    # the full first-to-last return interval rather than only eligible bars.
    annualized = 0.0 if strategy_type == "intraday_scalp" else (final_equity / initial_capital) ** (annualizer / max(len(bars) - 1, 1)) - 1
    # Returns are indexed by their ending bar, so include the interval ending
    # on the first eligible strategy bar in the Sharpe sample.
    metric_start = max(comparison_start - 1, 0) if strategy_type == "trend_filter" else 0
    metric_returns = returns[metric_start:]
    result = {
        "initialCapital": initial_capital,
        "finalEquity": float(final_equity),
        "totalReturnPct": float((final_equity / initial_capital - 1) * 100),
        "annualizedReturnPct": None if strategy_type == "intraday_scalp" else float(annualized * 100),
        "maxDrawdownPct": float(drawdowns.min() * 100),
        "sharpe": None if strategy_type == "intraday_scalp" else float(np.sqrt(annualizer) * metric_returns.mean() / metric_returns.std()) if metric_returns.size and metric_returns.std() else 0.0,
        "winRatePct": float(np.mean([trade["returnPct"] > 0 for trade in trades]) * 100) if trades else 0.0,
        "tradeCount": len(trades),
        "exposurePct": float(np.mean(exposure_curve) * 100) if exposure_curve else 0.0,
        "openPosition": bool(shares > 0),
        "openPositions": ([{
            "entry": entry_time,
            "entryPrice": float(entry_price),
            "currentPrice": float(close[-1]),
            "entryCapital": float(entry_value),
            "totalPnl": float(realized_proceeds + shares * close[-1] - entry_value),
            "returnPct": float((realized_proceeds + shares * close[-1] - entry_value) / entry_value * 100) if entry_value else 0.0,
        }] if shares > 0 else []),
        "equityCurve": equity,
        "trades": trades,
        "strategyType": strategy_type,
        "caveat": template["caveat"],
    }
    if strategy_type == "intraday_scalp":
        result["sampleWarning"] = "仅回测当前可获取的当日分钟样本；该结果不能代表长期策略表现。"
    if strategy_type == "trend_filter":
        benchmark_curve = np.full(len(close), initial_capital, dtype=float)
        benchmark_curve[comparison_start:] = initial_capital * close[comparison_start:] / close[comparison_start]
        benchmark_drawdown = benchmark_curve / np.maximum.accumulate(benchmark_curve) - 1
        result["benchmark"] = {
            "name": "买入持有",
            "totalReturnPct": float((benchmark_curve[-1] / initial_capital - 1) * 100),
            "maxDrawdownPct": float(benchmark_drawdown.min() * 100),
            "equityCurve": [
                {"time": point["time"], "value": float(value)}
                for point, value in zip(equity, benchmark_curve)
            ],
        }
        result["positionSizingTargetVolatilityPct"] = 20.0
        result["rebalanceCount"] = rebalance_count
    return result


def analyze_strategy(
    bars: pd.DataFrame,
    strategy_type: str,
    fast_period: int,
    slow_period: int,
    symbol: str,
    timeframe: str,
) -> dict[str, Any]:
    """Evaluate the selected daily strategy against the latest completed bar."""
    template = get_strategy_template(strategy_type)
    if timeframe not in template["supportedTimeframes"]:
        return {
            "supported": False,
            "signal": "当前周期不适用",
            "message": template["scopeError"],
            "asOf": None,
        }
    if template["supportedSymbols"] and symbol not in template["supportedSymbols"]:
        return {
            "supported": False,
            "signal": "当前标的不适用",
            "message": template["scopeError"],
            "asOf": None,
        }
    if fast_period < 2 or slow_period <= fast_period or slow_period > 250:
        raise ValueError("均线周期需满足 2 <= 快线 < 慢线 <= 250")
    if len(bars) < 2:
        return {
            "supported": True,
            "signal": "数据不足",
            "message": "至少需要两根已完成K线才能计算策略信号。",
            "asOf": None,
        }
    _validate_bars(bars)

    close = bars["close"].to_numpy(dtype=np.float64)
    high = bars["high"].to_numpy(dtype=np.float64)
    low = bars["low"].to_numpy(dtype=np.float64)
    latest = len(bars) - 1
    regime_period = 200 if len(bars) >= 200 else slow_period
    result: dict[str, Any] = {
        "supported": True,
        "strategyType": strategy_type,
        "asOf": bars.iloc[latest]["datetime"].isoformat(),
        "price": float(close[latest]),
        "basis": "分钟K" if strategy_type == "intraday_scalp" else "日K",
        "entryCondition": False,
        "exitCondition": False,
    }

    if strategy_type == "intraday_scalp":
        ema_fast = talib.EMA(close, timeperiod=fast_period)
        ema_slow = talib.EMA(close, timeperiod=slow_period)
        vwap = _session_vwap(bars)
        if latest < 1 or not np.isfinite([ema_fast[latest], ema_slow[latest], ema_fast[latest - 1], ema_slow[latest - 1], vwap[latest]]).all():
            result.update({"signal": "数据不足", "message": "当前分钟样本不足以计算所选均线和VWAP。"})
            return result
        entry = bool(ema_fast[latest] > ema_slow[latest] and ema_fast[latest - 1] <= ema_slow[latest - 1] and close[latest] > vwap[latest])
        exit_ = bool(ema_fast[latest] < ema_slow[latest] or close[latest] < vwap[latest])
        result.update({"entryCondition": entry, "exitCondition": exit_, "signal": "日内入场条件触发" if entry else "日内退出条件触发" if exit_ else "等待分钟信号", "message": f"EMA{fast_period} {ema_fast[latest]:.4f} / EMA{slow_period} {ema_slow[latest]:.4f}；VWAP {vwap[latest]:.4f}。只做日内，不持仓过夜。"})
        return result

    fast = talib.SMA(close, timeperiod=fast_period)
    slow = talib.SMA(close, timeperiod=slow_period)
    regime_sma = talib.SMA(close, timeperiod=60 if strategy_type == "swing_breakout" else regime_period)
    atr = talib.ATR(high, low, close, timeperiod=20)
    if strategy_type == "swing_breakout":
        entry, exit_ = _strategy_signals(
            strategy_type, len(bars), close, fast, slow, regime_sma, template,
            high=high, volume=bars["volume"].to_numpy(dtype=np.float64), atr=atr,
            fast_period=fast_period, slow_period=slow_period,
        )
        if not np.isfinite([fast[latest], regime_sma[latest], atr[latest]]).all():
            result.update({"signal": "数据不足", "message": "当前样本不足以计算日K波段条件。"})
            return result
        result.update({"entryCondition": entry, "exitCondition": exit_, "signal": "波段入场条件触发" if entry else "波段风险退出触发" if exit_ else "等待日K突破", "message": f"收盘 {close[latest]:.3f}；SMA60 {regime_sma[latest]:.3f}；ATR20 {atr[latest]:.3f}。信号在下一根日K开盘执行。"})
        return result

    if strategy_type == "trend_filter":
        if latest < 1 or not np.isfinite([close[latest], close[latest - 1], slow[latest], slow[latest - 1], regime_sma[latest], regime_sma[latest - 1]]).all():
            result.update({"signal": "数据不足", "message": f"至少需要 {regime_period} 根日K预热趋势均线。", "regime": "data_insufficient", "action": "observe"})
            return result
        entry = bool(
            close[latest] > slow[latest]
            and close[latest - 1] > slow[latest - 1]
        )
        macro_bull = bool(
            close[latest] > regime_sma[latest]
            and regime_sma[latest] >= regime_sma[latest - 1]
        )
        daily_return = close[latest] / close[latest - 1] - 1
        rebound_threshold = max(
            0.03,
            0.8 * atr[latest] / close[latest - 1]
            if np.isfinite(atr[latest]) else 0.03,
        )
        rebound = bool(daily_return >= rebound_threshold and not macro_bull)
        exit_confirmed = bool(
            close[latest] <= slow[latest] and close[latest - 1] <= slow[latest - 1]
        )
        regime = "bull" if macro_bull else "rebound_watch" if rebound else "bear"
        if entry:
            signal = "趋势持有条件满足" if macro_bull else "中期趋势转强 · 长期熊市观察"
            action = "hold"
        elif exit_confirmed:
            signal = "反弹观察，中期趋势离场已确认" if rebound else "中期趋势离场条件满足"
            action = "exit"
        else:
            signal = "反弹观察" if rebound else "趋势转弱，等待确认"
            action = "observe"
        result.update({
            "entryCondition": entry,
            "exitCondition": exit_confirmed,
            "signal": signal,
            "message": (
                f"收盘价 {close[latest]:.3f}，SMA{slow_period} 为 {slow[latest]:.3f}，"
                f"SMA{regime_period} 为 {regime_sma[latest]:.3f}；"
                + ("出现显著反弹，但尚未完成趋势修复。" if rebound else "离场需要连续两根弱势收盘确认。")
            ),
            "regime": regime,
            "action": action,
            "rebound": rebound,
            "regimeSmaPeriod": regime_period,
        })
        return result

    raise ValueError("不支持的策略类型")



def _rolling_performance(
    dates: list[str],
    equity: np.ndarray,
    benchmark: np.ndarray,
    exposure_pct: np.ndarray,
    transaction_costs: np.ndarray,
    start_time: str,
    end_time: str,
) -> list[dict[str, Any]]:
    """Compare strategy and buy-and-hold across equal, common out-of-sample windows."""
    indexes = np.flatnonzero((np.asarray(dates) >= start_time) & (np.asarray(dates) <= end_time))
    if indexes.size < 4:
        return []
    periods = np.array_split(indexes, min(4, indexes.size))
    results = []
    for period_index, period in enumerate(periods, start=1):
        first = int(period[0])
        last = int(period[-1])
        equity_base = equity[first - 1] if first else equity[first]
        benchmark_base = benchmark[first - 1] if first else benchmark[first]
        values = np.concatenate(([equity_base], equity[first:last + 1]))
        peaks = np.maximum.accumulate(values)
        drawdowns = (values - peaks) / peaks
        results.append({
            "period": period_index,
            "start": dates[first],
            "end": dates[last],
            "returnPct": float((equity[last] / equity_base - 1) * 100),
            "benchmarkReturnPct": float((benchmark[last] / benchmark_base - 1) * 100),
            "maxDrawdownPct": float(drawdowns.min() * 100),
            "exposurePct": float(np.mean(exposure_pct[first:last + 1])),
            "transactionCost": float(np.sum(transaction_costs[first:last + 1])),
        })
    return results


def _strategy_signals(
    strategy_type: str,
    index: int,
    close: np.ndarray,
    fast: np.ndarray,
    slow: np.ndarray,
    regime_sma: np.ndarray,
    template: dict[str, Any],
    *,
    fast_period: int = 20,
    slow_period: int = 60,
    high: np.ndarray | None = None,
    volume: np.ndarray | None = None,
    ema_fast: np.ndarray | None = None,
    ema_slow: np.ndarray | None = None,
    atr: np.ndarray | None = None,
    vwap: np.ndarray | None = None,
) -> tuple[bool, bool]:
    """Evaluate the selected strategy on the signal bar before next-open execution."""
    current = index - 1
    previous = current - 1
    if strategy_type == "swing_breakout":
        if current < max(slow_period, 60) or high is None or volume is None or atr is None:
            return False, False
        prior_high = np.max(high[current - slow_period:current])
        volume_base = np.mean(volume[current - 20:current]) if current >= 20 else np.nan
        entry = bool(close[current] > prior_high and close[current] > regime_sma[current] and volume[current] > volume_base * 1.2)
        exit_ = bool(close[current] < fast[current] or close[current] < np.max(close[max(0, current - 20):current + 1]) - 2 * atr[current])
        return entry, exit_
    if strategy_type == "intraday_scalp":
        if ema_fast is None or ema_slow is None or vwap is None or current < 1:
            return False, False
        values = [ema_fast[current], ema_slow[current], ema_fast[previous], ema_slow[previous], vwap[current], close[current]]
        if not np.isfinite(values).all():
            return False, False
        entry = ema_fast[current] > ema_slow[current] and ema_fast[previous] <= ema_slow[previous] and close[current] > vwap[current]
        exit_ = ema_fast[current] < ema_slow[current] or close[current] < vwap[current]
        return bool(entry), bool(exit_)
    if strategy_type == "trend_filter":
        if previous < 0 or not np.isfinite([
            close[current], close[previous], slow[current], slow[previous],
            regime_sma[current], regime_sma[previous],
        ]).all():
            return False, False
        entry = bool(
            close[current] > slow[current]
            and close[previous] > slow[previous]
        )
        exit_confirmed = bool(
            close[current] <= slow[current] and close[previous] <= slow[previous]
        )
        return entry, exit_confirmed

    raise ValueError("不支持的策略类型")


def _validate_bars(bars: pd.DataFrame) -> None:
    """Reject malformed OHLCV input before indicators or equity calculations run."""
    missing = [column for column in REQUIRED_BAR_COLUMNS if column not in bars.columns]
    if missing:
        raise ValueError(f"K线数据缺少字段：{', '.join(missing)}")
    if len(bars) < 2:
        raise ValueError("至少需要两根K线才能回测")
    numeric = bars[["open", "high", "low", "close", "volume"]].to_numpy(dtype=float)
    timestamps = pd.to_datetime(bars["datetime"], errors="coerce")
    if not np.isfinite(numeric).all() or timestamps.isna().any():
        raise ValueError("K线数据包含空值或非有限数值")
    if np.any(numeric[:, :4] <= 0) or np.any(numeric[:, 4] < 0):
        raise ValueError("K线价格必须为正数，成交量不能为负数")
    if np.any(numeric[:, 1] < np.maximum(numeric[:, 0], numeric[:, 3])) or np.any(numeric[:, 2] > np.minimum(numeric[:, 0], numeric[:, 3])):
        raise ValueError("K线最高价和最低价与开盘价、收盘价不一致")
    if not timestamps.is_monotonic_increasing or timestamps.duplicated().any():
        raise ValueError("K线时间必须严格递增且不能重复")

def _annualizer(interval_minutes: float) -> int:
    """Choose annual periods from the observed bar spacing for minute through month bars."""
    if interval_minutes <= 1.5:
        return 252 * 240
    if interval_minutes <= 3 * 24 * 60:
        return 252
    if interval_minutes <= 10 * 24 * 60:
        return 52
    return 12


def _session_vwap(bars: pd.DataFrame) -> np.ndarray:
    """Calculate a volume-weighted average price that resets at each session."""
    typical = (bars["high"].to_numpy(float) + bars["low"].to_numpy(float) + bars["close"].to_numpy(float)) / 3
    volume = bars["volume"].to_numpy(float)
    dates = pd.to_datetime(bars["datetime"]).dt.date.to_numpy()
    result = np.full(len(bars), np.nan)
    for date in np.unique(dates):
        indexes = np.flatnonzero(dates == date)
        cumulative_volume = np.cumsum(volume[indexes])
        cumulative_value = np.cumsum(typical[indexes] * volume[indexes])
        result[indexes] = np.divide(cumulative_value, cumulative_volume, out=typical[indexes].copy(), where=cumulative_volume > 0)
    return result
