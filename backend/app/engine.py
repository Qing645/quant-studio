"""Reproducible, long-only research backtest for editable signal rules."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import talib

from .strategies import get_strategy_template
from .ml import walk_forward_probabilities

def run_backtest(
    bars: pd.DataFrame,
    initial_capital: float,
    fee_rate: float,
    slippage_bps: float,
    fast_period: int,
    slow_period: int,
    strategy_type: str = "ml_direction",
) -> dict[str, Any]:
    """Simulate next-bar execution and return portfolio and trade statistics."""
    if fast_period < 2 or slow_period <= fast_period or slow_period > 250:
        raise ValueError("均线周期需满足 2 <= 快线 < 慢线 <= 250")
    if strategy_type not in {"growth_momentum", "trend_filter", "ml_direction"}:
        raise ValueError("不支持的策略类型")
    if initial_capital <= 0 or not 0 <= fee_rate <= 0.02 or not 0 <= slippage_bps <= 500:
        raise ValueError("资金、手续费或滑点参数超出允许范围")
    close = bars["close"].to_numpy(dtype=np.float64)
    open_price = bars["open"].to_numpy(dtype=np.float64)
    fast = talib.SMA(close, timeperiod=fast_period)
    slow = talib.SMA(close, timeperiod=slow_period)
    regime_sma = slow
    macd_hist = talib.MACD(close, fastperiod=12, slowperiod=26, signalperiod=9)[2]
    rsi = talib.RSI(close, timeperiod=14)
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
    ml_probabilities = np.full(len(bars), np.nan, dtype=float)
    ml_diagnostics: dict[str, Any] | None = None
    if strategy_type == "ml_direction":
        ml_probabilities, ml_diagnostics = walk_forward_probabilities(bars)

    # A signal on bar N executes at bar N+1 open to avoid same-bar lookahead.
    for index in range(len(bars)):
        if index > 1:
            if strategy_type == "ml_direction":
                probability = ml_probabilities[index - 1]
                crossed_up = np.isfinite(probability) and probability >= 0.55
                crossed_down = np.isfinite(probability) and probability < 0.45
            else:
                crossed_up, crossed_down = _strategy_signals(
                    strategy_type, index, close, fast, slow, regime_sma, macd_hist, rsi, template,
                )
            execution_price = open_price[index] * (1 + cost_rate)
            if shares == 0 and crossed_up:
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
            elif shares > 0 and crossed_down:
                exit_price = open_price[index] * (1 - cost_rate)
                proceeds = realized_proceeds + shares * exit_price
                exit_cost = float(shares * open_price[index] * cost_rate)
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
    annualized = (final_equity / initial_capital) ** (annualizer / max(len(bars) - comparison_start, 1)) - 1
    # Returns are indexed by their ending bar, so include the interval ending
    # on the first eligible strategy bar in the Sharpe sample.
    metric_start = max(comparison_start - 1, 0) if strategy_type == "trend_filter" else 0
    metric_returns = returns[metric_start:]
    result = {
        "initialCapital": initial_capital,
        "finalEquity": float(final_equity),
        "totalReturnPct": float((final_equity / initial_capital - 1) * 100),
        "annualizedReturnPct": float(annualized * 100),
        "maxDrawdownPct": float(drawdowns.min() * 100),
        "sharpe": float(np.sqrt(annualizer) * metric_returns.mean() / metric_returns.std()) if metric_returns.size and metric_returns.std() else 0.0,
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
    if ml_diagnostics is not None:
        result["machineLearning"] = ml_diagnostics
        result["_exposureCurve"] = exposure_curve
        result["_transactionCostCurve"] = transaction_cost_curve
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
        "basis": "日K",
        "entryCondition": False,
        "exitCondition": False,
    }

    if strategy_type == "ml_direction":
        try:
            probabilities, diagnostics = walk_forward_probabilities(bars)
        except ValueError as exc:
            result.update({"signal": "数据不足", "message": str(exc)})
            return result
        probability = probabilities[latest]
        if not np.isfinite(probability):
            result.update({"signal": "暂无样本外预测", "message": "当前样本尚未生成有效的样本外概率。"})
            return result
        entry = bool(probability >= 0.55)
        exit_ = bool(probability < 0.45)
        result.update({
            "entryCondition": entry,
            "exitCondition": exit_,
            "probabilityPct": float(probability * 100),
            "signal": "达到入场阈值" if entry else "达到退出阈值" if exit_ else "处于观望区间",
            "message": f"最新样本外上涨概率 {probability * 100:.1f}%；入场阈值 55%，退出阈值 45%。",
            "diagnostics": diagnostics,
        })
        return result

    fast = talib.SMA(close, timeperiod=fast_period)
    slow = talib.SMA(close, timeperiod=slow_period)
    regime_sma = talib.SMA(close, timeperiod=regime_period)
    atr = talib.ATR(high, low, close, timeperiod=20)
    macd_hist = talib.MACD(close, fastperiod=12, slowperiod=26, signalperiod=9)[2]
    rsi = talib.RSI(close, timeperiod=14)
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

    entry, exit_ = _strategy_signals(
        strategy_type, len(bars), close, fast, slow, regime_sma, macd_hist, rsi, template,
    )
    if not np.isfinite([fast[latest], slow[latest], macd_hist[latest], rsi[latest]]).all():
        result.update({"signal": "数据不足", "message": "当前样本不足以计算完整的动量条件。"})
        return result
    result.update({
        "entryCondition": entry,
        "exitCondition": exit_,
        "signal": "开仓条件触发" if entry else "离场条件触发" if exit_ else "等待信号",
        "message": f"SMA{fast_period} {fast[latest]:.3f} / SMA{slow_period} {slow[latest]:.3f}；MACD柱 {macd_hist[latest]:.4f}；RSI14 {rsi[latest]:.1f}。",
    })
    return result


def run_ml_portfolio(
    bars_by_symbol: dict[str, pd.DataFrame],
    initial_capital: float,
    fee_rate: float,
    slippage_bps: float,
    fast_period: int,
    slow_period: int,
) -> dict[str, Any]:
    """Backtest independent symbol models with equal initial capital per eligible symbol."""
    candidates: dict[str, dict[str, Any]] = {}
    excluded: list[dict[str, str]] = []
    for symbol, bars in bars_by_symbol.items():
        try:
            candidates[symbol] = run_backtest(
                bars, 1.0, fee_rate, slippage_bps, fast_period, slow_period, "ml_direction",
            )
        except ValueError as exc:
            excluded.append({"symbol": symbol, "reason": str(exc)})
    if not candidates:
        reasons = "；".join(f"{item['symbol']}: {item['reason']}" for item in excluded)
        raise ValueError(f"所选标的均无法训练：{reasons}")

    allocation = initial_capital / len(candidates)
    dates = sorted({
        point["time"] for result in candidates.values() for point in result["equityCurve"]
    })
    total_equity = pd.Series(0.0, index=dates)
    total_benchmark = pd.Series(0.0, index=dates)
    trades = []
    symbol_results = []
    diagnostics = []
    open_positions = 0
    open_position_details = []
    total_exposure_value = pd.Series(0.0, index=dates)
    total_transaction_costs = pd.Series(0.0, index=dates)
    for symbol, result in candidates.items():
        equity = pd.Series(
            [point["value"] * allocation for point in result["equityCurve"]],
            index=[point["time"] for point in result["equityCurve"]],
        )
        # Unlisted dates keep the allocated cash balance until the instrument has data.
        aligned = equity.reindex(dates).ffill().fillna(allocation)
        total_equity = total_equity.add(aligned, fill_value=0)
        position_curve = pd.Series(
            result["_exposureCurve"],
            index=[point["time"] for point in result["equityCurve"]],
        ).reindex(dates).ffill().fillna(0.0)
        total_exposure_value = total_exposure_value.add(position_curve * allocation, fill_value=0)
        cost_curve = pd.Series(
            result["_transactionCostCurve"],
            index=[point["time"] for point in result["equityCurve"]],
        ).reindex(dates).fillna(0.0) * allocation
        total_transaction_costs = total_transaction_costs.add(cost_curve, fill_value=0)
        closes = pd.Series(
            bars_by_symbol[symbol]["close"].to_numpy(dtype=float),
            index=pd.to_datetime(bars_by_symbol[symbol]["datetime"]).dt.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        benchmark = allocation * closes / closes.iloc[0]
        aligned_benchmark = benchmark.reindex(dates).ffill().fillna(allocation)
        total_benchmark = total_benchmark.add(aligned_benchmark, fill_value=0)
        scaled_trades = [{
            **trade,
            "symbol": symbol,
            "pnl": float(trade["pnl"] * allocation),
            "transactionCost": float(trade["transactionCost"] * allocation),
        } for trade in result["trades"]]
        trades.extend(scaled_trades)
        open_positions += int(result["openPosition"])
        open_position_details.extend({
            **position,
            "symbol": symbol,
            "entryCapital": float(position["entryCapital"] * allocation),
            "totalPnl": float(position["totalPnl"] * allocation),
        } for position in result["openPositions"])
        diagnostics.append(result["machineLearning"])
        symbol_results.append({
            "symbol": symbol,
            "initialCapital": allocation,
            "finalEquity": float(result["finalEquity"] * allocation),
            "totalReturnPct": float(result["totalReturnPct"]),
            "tradeCount": result["tradeCount"],
            "openPosition": result["openPosition"],
            "buyHoldReturnPct": float((closes.iloc[-1] / closes.iloc[0] - 1) * 100),
            "machineLearning": result["machineLearning"],
        })

    curve = total_equity.to_numpy(dtype=float)
    peaks = np.maximum.accumulate(curve)
    drawdowns = (curve - peaks) / peaks
    returns = np.diff(curve) / curve[:-1] if len(curve) > 1 else np.array([])
    interval_minutes = pd.Series(pd.to_datetime(dates)).diff().dt.total_seconds().median() / 60
    annualizer = _annualizer(interval_minutes)
    final_equity = float(curve[-1])
    benchmark_curve = total_benchmark.to_numpy(dtype=float)
    benchmark_return = float((benchmark_curve[-1] / initial_capital - 1) * 100)
    benchmark_drawdowns = (benchmark_curve - np.maximum.accumulate(benchmark_curve)) / np.maximum.accumulate(benchmark_curve)
    sample_count = sum(item["outOfSampleSamples"] for item in diagnostics)
    weighted_accuracy = sum(item["accuracyPct"] * item["outOfSampleSamples"] for item in diagnostics) / sample_count
    weighted_brier = sum(item["brierScore"] * item["outOfSampleSamples"] for item in diagnostics) / sample_count
    common_start = max(item["outOfSampleStart"] for item in diagnostics)
    common_end = min(item["outOfSampleEnd"] for item in diagnostics)
    rolling_performance = _rolling_performance(
        dates,
        curve,
        benchmark_curve,
        total_exposure_value.to_numpy(dtype=float) / curve * 100,
        total_transaction_costs.to_numpy(dtype=float),
        common_start,
        common_end,
    )
    return {
        "initialCapital": initial_capital,
        "finalEquity": final_equity,
        "totalReturnPct": float((final_equity / initial_capital - 1) * 100),
        "annualizedReturnPct": float(((final_equity / initial_capital) ** (annualizer / max(len(curve), 1)) - 1) * 100),
        "maxDrawdownPct": float(drawdowns.min() * 100),
        "sharpe": float(np.sqrt(annualizer) * returns.mean() / returns.std()) if returns.size and returns.std() else 0.0,
        "winRatePct": float(np.mean([trade["returnPct"] > 0 for trade in trades]) * 100) if trades else 0.0,
        "tradeCount": len(trades),
        "exposurePct": float(np.mean(total_exposure_value.to_numpy(dtype=float) / curve) * 100),
        "openPosition": open_positions > 0,
        "openPositions": open_position_details,
        "equityCurve": [{"time": time, "value": float(value)} for time, value in total_equity.items()],
        "trades": sorted(trades, key=lambda trade: trade["entry"]),
        "strategyType": "ml_direction",
        "symbols": [item["symbol"] for item in symbol_results],
        "symbolResults": symbol_results,
        "benchmark": {
            "name": "等权买入持有",
            "totalReturnPct": benchmark_return,
            "maxDrawdownPct": float(benchmark_drawdowns.min() * 100),
            "equityCurve": [{"time": time, "value": float(value)} for time, value in total_benchmark.items()],
        },
        "machineLearning": {
            "horizonBars": 5,
            "outOfSampleSamples": sample_count,
            "accuracyPct": float(weighted_accuracy),
            "balancedAccuracyPct": float(np.mean([item["balancedAccuracyPct"] for item in diagnostics])),
            "brierScore": float(weighted_brier),
            "outOfSampleStart": min(item["outOfSampleStart"] for item in diagnostics),
            "outOfSampleEnd": max(item["outOfSampleEnd"] for item in diagnostics),
            "latestUpProbabilityPct": None,
            "caveat": "指标按各标的样本外结果汇总；组合对标的等分初始资金，未按期再平衡。",
        },
        "rollingPerformance": rolling_performance,
        "caveat": "多标的等分初始资金，各自独立训练；未按期再平衡，未建模涨跌停、分红税和市场冲击。",
        "excludedSymbols": excluded,
    }


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
    macd_hist: np.ndarray,
    rsi: np.ndarray,
    template: dict[str, Any],
) -> tuple[bool, bool]:
    """Evaluate the selected strategy on the signal bar before next-open execution."""
    current = index - 1
    previous = current - 1
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

    values = [close[current], fast[current], slow[current], macd_hist[previous], macd_hist[current], rsi[current]]
    if not np.isfinite(values).all():
        return False, False
    crossed_positive = macd_hist[previous] <= 0 < macd_hist[current]
    crossed_negative = macd_hist[previous] >= 0 > macd_hist[current]
    crossed_up = (
        crossed_positive and fast[current] > slow[current] and close[current] > slow[current]
        and template["entryRsiMin"] <= rsi[current] <= template["entryRsiMax"]
    )
    crossed_down = (
        close[current] < fast[current] or crossed_negative or rsi[current] < template["exitRsiBelow"]
    )
    return bool(crossed_up), bool(crossed_down)


def _annualizer(interval_minutes: float) -> int:
    """Choose annual periods from the observed bar spacing for minute through month bars."""
    if interval_minutes <= 1.5:
        return 252 * 240
    if interval_minutes <= 3 * 24 * 60:
        return 252
    if interval_minutes <= 10 * 24 * 60:
        return 52
    return 12
