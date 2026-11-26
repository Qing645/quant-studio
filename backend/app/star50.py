"""Causal daily breakout signals and a separate ATR risk ledger for 588080 research."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import numpy as np
import pandas as pd
import talib

from .strategies import get_strategy_template


class Star50Signals:
    """Compute trailing signals once, shared by live observation and the account simulation."""

    def __init__(self, bars: pd.DataFrame, fast: int, slow: int, params: dict[str, Any]) -> None:
        """Exclude the signal candle from breakout and volume reference windows."""
        self.close = bars["close"].to_numpy(dtype=float)
        high, low = bars["high"].to_numpy(dtype=float), bars["low"].to_numpy(dtype=float)
        self.fast = talib.SMA(self.close, timeperiod=fast)
        self.slow = talib.SMA(self.close, timeperiod=slow)
        self.atr = talib.ATR(high, low, self.close, timeperiod=20)
        self.prior_high = bars["high"].shift(1).rolling(params["breakoutPeriod"]).max().to_numpy()
        self.prior_volume = bars["volume"].shift(1).rolling(20).mean().to_numpy()
        prior_slow = pd.Series(self.slow).shift(params["slopeBars"]).to_numpy()
        prior_fast = pd.Series(self.fast).shift(1).to_numpy()
        prior_close = pd.Series(self.close).shift(1).to_numpy()
        self.ready = np.isfinite(np.column_stack((self.slow, prior_slow, self.prior_high,
                                                 self.prior_volume, self.fast, prior_fast, self.atr))).all(axis=1)
        self.ready &= (self.atr > 0) & (self.prior_volume > 0)
        self.trend = (self.close > self.slow) & (self.slow > prior_slow)
        self.breakout = self.close > self.prior_high
        self.volume = bars["volume"].to_numpy() >= self.prior_volume * params["volumeMultiplier"]
        self.entry = self.ready & self.trend & self.breakout & self.volume
        self.exit = self.ready & (self.close <= self.fast) & (prior_close <= prior_fast)
        self.initial_stop = self.close - params["initialAtrMultiplier"] * self.atr


class Star50PullbackSignals:
    """Confirm oversold pullbacks inside a long trend, shared by live and simulated execution."""

    def __init__(self, bars: pd.DataFrame, fast: int, slow: int, params: dict[str, Any]) -> None:
        """Use each completed close for SMA/RSI; keep signal-day ATR frozen for the next open."""
        self.close = bars["close"].to_numpy(dtype=float)
        self.fast = talib.SMA(self.close, timeperiod=fast)
        self.slow = talib.SMA(self.close, timeperiod=slow)
        self.rsi = talib.RSI(self.close, timeperiod=2)
        self.atr = talib.ATR(bars["high"].to_numpy(dtype=float), bars["low"].to_numpy(dtype=float),
                             self.close, timeperiod=20)
        self.ready = np.isfinite(np.column_stack((self.fast, self.slow, self.rsi, self.atr))).all(axis=1)
        self.ready &= self.atr > 0
        self.trend = self.close > self.slow
        self.oversold = self.rsi <= params["rsiThreshold"]
        self.entry = self.ready & self.trend & self.oversold
        self.exit = self.ready & (self.close >= self.fast)
        self.initial_stop = self.close - params["initialAtrMultiplier"] * self.atr


class Star50ChannelSignals:
    """Hold price-channel trends only when a long moving-average regime permits new entries."""

    def __init__(self, bars: pd.DataFrame, fast: int, slow: int, params: dict[str, Any]) -> None:
        """Exclude the current candle from both channels and the regime average."""
        self.close = bars["close"].to_numpy(dtype=float)
        self.atr = talib.ATR(bars["high"].to_numpy(dtype=float), bars["low"].to_numpy(dtype=float),
                             self.close, timeperiod=20)
        self.prior_high = bars["high"].shift(1).rolling(slow).max().to_numpy()
        self.prior_low = bars["low"].shift(1).rolling(fast).min().to_numpy()
        self.prior_volume = bars["volume"].shift(1).rolling(20).mean().to_numpy()
        self.regime = talib.SMA(self.close, timeperiod=params["trendFilterPeriod"])
        prior_regime = pd.Series(self.regime).shift(params["trendSlopeBars"]).to_numpy()
        self.prior_regime = prior_regime
        self.ready = np.isfinite(np.column_stack((self.prior_high, self.prior_low, self.atr,
                                                  self.prior_volume, self.regime, prior_regime))).all(axis=1)
        self.ready &= (self.atr > 0) & (self.prior_volume > 0)
        self.trend = self.close > self.regime
        self.trend_rising = self.regime > prior_regime
        self.breakout = self.close > self.prior_high
        self.volume_ratio = bars["volume"].to_numpy(dtype=float) / self.prior_volume
        self.volume_confirmed = self.volume_ratio >= params["volumeMultiplier"]
        self.entry = self.ready & self.trend & self.trend_rising & self.breakout & self.volume_confirmed
        self.exit = self.ready & (self.close < self.prior_low)
        self.initial_stop = self.close - params["initialAtrMultiplier"] * self.atr


def _signals(bars: pd.DataFrame, fast: int, slow: int, params: dict[str, Any], strategy_type: str):
    """Select an explicit rule identity; legacy saved strategies retain their original calculations."""
    if strategy_type == "star50_pullback":
        return Star50PullbackSignals(bars, fast, slow, params)
    if strategy_type == "star50_breakout":
        return Star50Signals(bars, fast, slow, params)
    if strategy_type == "star50_channel":
        return Star50ChannelSignals(bars, fast, slow, params)
    raise ValueError("不支持的科创50策略版本")


@dataclass
class ATRPosition:
    """Keep the frozen initial stop and a nondecreasing line for one actual simulated holding."""

    entry_day: Any
    initial_stop: float
    stop: float
    highest_close: float = -math.inf

    def advance(self, close: float, atr: float, multiplier: float) -> bool:
        """Update only on completed held candles; expanding ATR must never loosen the stop."""
        self.highest_close = max(self.highest_close, close)
        self.stop = max(self.stop, self.highest_close - multiplier * atr)
        return close <= self.stop


def analyze_star50(bars: pd.DataFrame, fast: int, slow: int, params: dict[str, Any],
                   strategy_type: str = "star50_breakout") -> dict[str, Any]:
    """Expose price conditions, never infer an account's holding-dependent ATR exit or size."""
    signals = _signals(bars, fast, slow, params, strategy_type)
    pullback = strategy_type == "star50_pullback"
    channel = strategy_type == "star50_channel"
    index = len(bars) - 1
    ready = bool(signals.ready[index])
    entry, exit_ = bool(signals.entry[index]), bool(signals.exit[index])
    reference = None
    if ready:
        reference = {"atr20": float(signals.atr[index]),
                     "initialStopReference": float(signals.initial_stop[index]),
                     "riskBudgetPct": params["riskBudgetPct"], "maxPositionPct": params["maxPositionPct"]}
        if channel:
            reference.update(priorBreakoutHigh=float(signals.prior_high[index]),
                             priorExitLow=float(signals.prior_low[index]), breakoutPassed=bool(signals.breakout[index]),
                             trendPassed=bool(signals.trend[index]), trendSlopePassed=bool(signals.trend_rising[index]),
                             volumePassed=bool(signals.volume_confirmed[index]),
                             volumeRatio=float(signals.volume_ratio[index]),
                             trendMean=float(signals.regime[index]),
                             priorTrendMean=float(signals.prior_regime[index]))
        elif pullback:
            reference.update(trendPassed=bool(signals.trend[index]), rsi2=float(signals.rsi[index]),
                             oversoldPassed=bool(signals.oversold[index]), recoveryMean=float(signals.fast[index]))
        else:
            reference.update(trendPassed=bool(signals.trend[index]), priorBreakoutHigh=float(signals.prior_high[index]),
                             breakoutPassed=bool(signals.breakout[index]), volumePassed=bool(signals.volume[index]))
    if channel:
        rule = (f"已完成日K收盘高于SMA{params['trendFilterPeriod']}、该均线高于{params['trendSlopeBars']}日前，"
                f"突破此前{slow}根最高价且成交量达到此前20根均量{params['volumeMultiplier']:g}倍时观察入场；"
                f"跌破此前{fast}根最低价或持仓ATR风险线后退出。")
        entry_text, waiting_text, exit_text = "趋势过滤后通道突破", "等待趋势过滤和通道突破", "价格通道退出条件满足 · 持仓待核验"
        warmup = max(params["trendFilterPeriod"], slow + 1, fast + 1, 21)
    elif pullback:
        rule = f"已完成日K确认；收盘高于SMA{slow}且RSI2≤{params['rsiThreshold']:g}时观察入场，收盘恢复至SMA{fast}时观察退出。"
        entry_text, waiting_text, exit_text = "顺势回撤", "等待顺势回撤条件", "均线退出条件满足 · 持仓待核验"
        warmup = max(slow, fast, 21)
    else:
        rule = f"已完成日K确认；趋势、前{params['breakoutPeriod']}根高点和此前20根均量共同过滤。"
        entry_text, waiting_text, exit_text = "趋势突破", "等待趋势突破条件", "均线退出条件满足 · 持仓待核验"
        warmup = max(slow + params['slopeBars'], params['breakoutPeriod'] + 1, fast + 1, 21)
    return {
        "supported": True, "strategyType": strategy_type, "parameters": params,
        "fastPeriod": fast, "slowPeriod": slow, "basis": "日K",
        "asOf": bars.iloc[index]["datetime"].isoformat(), "price": float(signals.close[index]),
        "entryCondition": entry, "exitCondition": exit_, "action": "observe",
        "positionRiskAvailable": False,
        "riskReference": reference,
        "signal": (f"{entry_text}条件满足 · 次日开盘待核验" if entry else exit_text if exit_ else waiting_text) if ready else "数据不足",
        "message": (
            rule +
            f"单次计划价格风险{params['riskBudgetPct']:g}%，开仓上限{params['maxPositionPct']:g}%，不是固定仓位。"
            "入场还需次日开盘跳空、现金和100份整数手检查。当前没有账户成本、持仓最高收盘价和可卖份额，"
            "只展示入场与均线退出条件，不判定实际持仓ATR风险退出、不推定实际仓位。"
        ) if ready else f"至少需{warmup}根有效已完成日K预热。",
    }


def channel_intraday_preview(bars: pd.DataFrame, fast: int, slow: int,
                             params: dict[str, Any], source_time: str) -> dict[str, Any]:
    """Explain today's channel gates using the same rules, with cumulative volume explicitly provisional."""
    observation = analyze_star50(bars, fast, slow, params, "star50_channel")
    reference = observation["riskReference"]
    details = None
    blockers = []
    if reference is not None:
        price = observation["price"]
        # Positive gap means a further rise is needed; a zero gap still fails strict breakout.
        details = {
            **reference,
            "breakoutGapPct": (reference["priorBreakoutHigh"] / price - 1) * 100,
            "exitBufferPct": (price / reference["priorExitLow"] - 1) * 100,
            "trendSlopePct": (reference["trendMean"] / reference["priorTrendMean"] - 1) * 100,
            "volumeThreshold": params["volumeMultiplier"],
        }
        for key, label in (("trendPassed", "价格未站上趋势均线"),
                           ("trendSlopePassed", "趋势均线尚未走升"),
                           ("breakoutPassed", "价格尚未突破上轨"),
                           ("volumePassed", "累计量尚未达到日量门槛")):
            if not reference[key]:
                blockers.append(label)
    return {
        "asOf": source_time, "price": observation["price"], "ready": reference is not None,
        "entryCondition": observation["entryCondition"], "exitCondition": observation["exitCondition"],
        "details": details, "blockers": blockers,
        "message": ("当日未收盘日K的暂态观察；累计成交量与此前20个完整交易日均量比较，"
                    "不是同一时刻量比，未达门槛不能判定全天缩量。价格通道下轨不是实际持仓ATR止损线；"
                    "正式信号仍以已完成日K为准。") if reference is not None else observation["message"],
    }


def _quote(price: float, side: str) -> float:
    """Apply a conservative 0.001 quote grid before fees, within the declared adjusted-price model."""
    return (math.ceil(price * 1000 - 1e-9) if side == "buy" else math.floor(price * 1000 + 1e-9)) / 1000


def _executable(bars: pd.DataFrame, index: int, side: str) -> bool:
    """Respect known opening restrictions and conservatively block directional 20% limit gaps.

    Daily OHLC cannot establish queue fills. Zero-volume candles are marked unavailable;
    other fills remain explicit research assumptions, not verified market executions.
    """
    row = bars.iloc[index]
    if row["volume"] <= 0:
        return False
    flag = f"{side}Executable"
    if flag in bars and not bool(row[flag]):
        return False
    if index:
        previous = float(bars.iloc[index - 1]["close"])
        limit = round(previous * (1.2 if side == "buy" else .8), 3)
        if (side == "buy" and row["open"] >= limit) or (side == "sell" and row["open"] <= limit):
            return False
    return True


def _summary(values: np.ndarray, initial: float) -> dict[str, Any]:
    """Calculate comparable close-marked research metrics, including the initial cash baseline."""
    curve = np.r_[initial, values]
    returns = np.diff(curve) / curve[:-1]
    drawdown = curve / np.maximum.accumulate(curve) - 1
    return {"totalReturnPct": float((curve[-1] / initial - 1) * 100),
            "annualizedReturnPct": float(((curve[-1] / initial) ** (252 / max(len(values) - 1, 1)) - 1) * 100),
            "maxDrawdownPct": float(drawdown.min() * 100),
            "sharpe": float(np.sqrt(252) * returns.mean() / returns.std()) if returns.std() else 0.0}


def _benchmark(bars: pd.DataFrame, initial: float, weight: float, start: int | None, cost: float) -> dict[str, Any]:
    """Buy the same declared opening model once; retain cash, round lots and include entry costs."""
    shares, cash = 0, initial
    values = []
    actual_start = None
    for index, row in enumerate(bars.itertuples()):
        if start is not None and index >= start and actual_start is None and _executable(bars, index, "buy"):
            price = _quote(row.open, "buy") * (1 + cost)
            shares = int(initial * weight / price // 100) * 100
            cash -= shares * price
            actual_start = row.datetime.isoformat()
        values.append(cash + shares * row.close)
    stats = _summary(np.asarray(values), initial)
    return {"name": "100%买入持有" if weight == 1 else "50%买入持有＋50%现金",
            "start": actual_start, **stats,
            "equityCurve": [{"time": row.datetime.isoformat(), "value": float(value)} for row, value in zip(bars.itertuples(), values)]}


def run_star50_backtest(
    bars: pd.DataFrame, initial: float, fee: float, slippage: float, fast: int, slow: int,
    params: dict[str, Any], *, include_sensitivity: bool = True, strategy_type: str = "star50_breakout",
) -> dict[str, Any]:
    """Simulate a single T+1 holding with next-open execution and persistent pending exits."""
    if bars["datetime"].dt.date.duplicated().any():
        raise ValueError("科创50策略必须使用每天一根的日K，不能传入分钟K")
    for flag in ("buyExecutable", "sellExecutable"):
        if flag in bars and not bars[flag].map(lambda value: isinstance(value, (bool, np.bool_))).all():
            raise ValueError(f"{flag}必须逐根提供布尔值")
    signals = _signals(bars, fast, slow, params, strategy_type)
    pullback = strategy_type == "star50_pullback"
    channel = strategy_type == "star50_channel"
    cost = fee + slippage / 10000
    cash, shares = initial, 0
    position: ATRPosition | None = None
    entry: dict[str, Any] | None = None
    pending_exit: str | None = None
    trades, executions, cancellations, risks, equity, exposures, costs = [], [], [], [], [], [], []
    for index, row in enumerate(bars.itertuples()):
        date = row.datetime.date()
        time = row.datetime.isoformat()
        bar_cost = 0.0
        # Open decisions read yesterday's completed signals and the new opening quote only.
        if position is not None and pending_exit and date > position.entry_day and _executable(bars, index, "sell"):
            raw_price = _quote(row.open, "sell")
            price = raw_price * (1 - cost)
            bar_cost = shares * raw_price * cost
            cash += shares * price
            pnl = shares * price - entry["entryCapital"]
            trades.append({"entry": entry["entry"], "exit": time, "entryPrice": entry["entryPrice"],
                           "exitPrice": price, "returnPct": pnl / entry["entryCapital"] * 100,
                           "pnl": pnl, "transactionCost": entry["entryCost"] + bar_cost,
                           "shares": shares, "exitReason": pending_exit, "initialRiskLine": position.initial_stop})
            executions.append({"time": time, "side": "sell", "price": price, "rawPrice": raw_price,
                               "shares": shares, "reason": pending_exit, "transactionCost": bar_cost,
                               "sellableShares": 0, "lockedShares": 0, "cash": cash})
            shares, position, entry, pending_exit = 0, None, None, None
            # The same opening cannot sell and re-enter. A later close must form a fresh signal.
        elif position is None and index and signals.entry[index - 1] and not signals.exit[index - 1]:
            signal_index = index - 1
            raw_price = _quote(row.open, "buy")
            stop = float(signals.initial_stop[signal_index])
            distance = raw_price - stop
            reason = None
            if not _executable(bars, index, "buy"):
                reason = "开盘成交条件不可用，入场信号作废"
            elif stop <= 0 or distance <= 0:
                reason = "开盘价或初始风险线无效"
            elif abs(raw_price - signals.close[signal_index]) > params["gapAtrMultiplier"] * signals.atr[signal_index]:
                reason = "开盘偏离超过信号ATR阈值"
            else:
                unit_cost = raw_price * (1 + cost)
                count = math.floor(min(cash * params["riskBudgetPct"] / 100 / distance,
                                       cash * params["maxPositionPct"] / 100 / unit_cost, cash / unit_cost) / 100) * 100
                if count < 100:
                    reason = "风险预算或现金不足100份"
                else:
                    shares = count
                    bar_cost = count * raw_price * cost
                    entry = {"entry": time, "entryPrice": unit_cost, "entryCapital": count * unit_cost,
                             "entryCost": bar_cost, "plannedPriceRisk": count * distance,
                             "signalAsOf": bars.iloc[signal_index]["datetime"].isoformat()}
                    cash -= entry["entryCapital"]
                    position = ATRPosition(date, stop, stop)
                    executions.append({"time": time, "side": "buy", "price": unit_cost, "rawPrice": raw_price,
                                       "shares": shares, "reason": "价格通道突破入场" if channel else "顺势超卖回撤入场" if pullback else "趋势突破入场", "transactionCost": bar_cost,
                                       "sellableShares": 0, "lockedShares": shares, "cash": cash,
                                       "initialRiskLine": stop, "plannedPriceRisk": count * distance,
                                       "signalAsOf": entry["signalAsOf"]})
            if reason:
                cancellations.append({"time": time, "reason": reason})
        # Closing state is never used to change the already simulated opening execution.
        if position is not None:
            risk_exit = position.advance(row.close, signals.atr[index], params["trailingAtrMultiplier"])
            if risk_exit:
                pending_exit = "ATR风险线退出"
            elif signals.exit[index] and pending_exit is None:
                pending_exit = f"跌破前{fast}根低点退出" if channel else f"恢复至SMA{fast}退出" if pullback else "连续两根均线退出"
            risk_line = position.stop
        else:
            risk_line = None
        value = cash + shares * row.close
        equity.append({"time": time, "value": float(value)})
        exposures.append(shares * row.close / value * 100)
        costs.append(bar_cost)
        risks.append({"time": time, "value": risk_line, "pendingExit": pending_exit})
    curve = np.asarray([point["value"] for point in equity])
    ready = np.flatnonzero(signals.ready)
    start = int(ready[0]) + 1 if ready.size and ready[0] + 1 < len(bars) else None
    benchmarks = [_benchmark(bars, initial, weight, start, cost) for weight in (1, .5)]
    result = {
        "initialCapital": initial, "finalEquity": float(curve[-1]), **_summary(curve, initial),
        "winRatePct": float(np.mean([trade["pnl"] > 0 for trade in trades]) * 100) if trades else 0.0,
        "tradeCount": len(trades), "exposurePct": float(np.mean(exposures)), "openPosition": bool(shares),
        "openPositions": ([{**entry, "currentPrice": float(signals.close[-1]),
                            "totalPnl": shares * signals.close[-1] - entry["entryCapital"],
                            "returnPct": (shares * signals.close[-1] / entry["entryCapital"] - 1) * 100,
                            "shares": shares, "riskLine": position.stop, "pendingExit": pending_exit}] if position else []),
        "equityCurve": equity, "trades": trades, "executions": executions, "strategyType": strategy_type,
        "parameters": params, "benchmark": benchmarks[0], "benchmarks": benchmarks,
        "riskManagement": {"riskBudgetPct": params["riskBudgetPct"], "maxPositionPct": params["maxPositionPct"],
                           "cancelledEntries": cancellations, "pendingExit": pending_exit, "riskLineCurve": risks,
                           "totalTransactionCost": float(sum(costs)),
                           "averageTradePnl": float(np.mean([trade["pnl"] for trade in trades])) if trades else 0.0,
                           "averageWinPnl": float(np.mean([trade["pnl"] for trade in trades if trade["pnl"] > 0])) if any(trade["pnl"] > 0 for trade in trades) else 0.0,
                           "averageLossPnl": float(np.mean([trade["pnl"] for trade in trades if trade["pnl"] <= 0])) if any(trade["pnl"] <= 0 for trade in trades) else 0.0,
                           "turnoverPct": float(sum(item["shares"] * item["rawPrice"] for item in executions) / initial * 100)},
        "caveat": get_strategy_template(strategy_type)["caveat"],
    }
    # Calendar slices are retrospective diagnostics; no claim of unseen/out-of-sample validation.
    result["researchPeriods"] = []
    dates = bars["datetime"].dt.strftime("%Y-%m-%d").to_numpy()
    for label, begin, end in (("2020—2023", "2020-01-01", "2023-12-31"),
                              ("2024", "2024-01-01", "2024-12-31"),
                              ("2025起", "2025-01-01", "2099-12-31")):
        indexes = np.flatnonzero((dates >= begin) & (dates <= end))
        if indexes.size:
            first, last = int(indexes[0]), int(indexes[-1])
            baseline = curve[first - 1] if first else initial
            stats = _summary(curve[first:last + 1], baseline)
            result["researchPeriods"].append({"label": label, "start": dates[first], "end": dates[last], **stats,
                                              "transactionCost": float(sum(costs[first:last + 1]))})
    if include_sensitivity:
        result["costSensitivity"] = []
        for label, scenario_fee, scenario_slippage in (("零成本", 0.0, 0.0),
                                                       ("双倍成本", min(fee * 2, .02), min(slippage * 2, 500))):
            scenario = run_star50_backtest(bars, initial, scenario_fee, scenario_slippage, fast, slow,
                                            params, include_sensitivity=False, strategy_type=strategy_type)
            result["costSensitivity"].append({"scenario": label, "totalReturnPct": scenario["totalReturnPct"],
                                               "maxDrawdownPct": scenario["maxDrawdownPct"]})
    if start is None:
        result["sampleWarning"] = "样本不足以完成预热并提供下一根执行日K，暂无可比较交易区间。"
    if pullback or channel:
        result["researchTarget"] = {"annualReturnPct": 15, "maxDrawdownPct": 20,
                                    "achieved": start is not None and result["annualizedReturnPct"] >= 15 and result["maxDrawdownPct"] >= -20}
    return result
