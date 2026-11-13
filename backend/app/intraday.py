"""Causal, sell-first base-position T research for domestic equity ETFs under T+1."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from typing import Any

import numpy as np
import pandas as pd
import talib

LOT_SIZE = 100
PRICE_TICK = .001
SELL_CUTOFF = time(14, 45)
RESTORE_TIME = time(14, 55)


def session_indicators(bars: pd.DataFrame, fast_period: int, slow_period: int, *,
                       continuous: bool = False) -> dict[str, np.ndarray]:
    """Reset VWAP each day; longer decision periods warm EMA/ATR with preceding sessions only."""
    values = {name: np.full(len(bars), np.nan) for name in ("fast", "slow", "atr", "vwap")}
    if bars.empty:
        return values
    dates = bars["datetime"].dt.date.to_numpy()
    for day in np.unique(dates):
        indexes = np.flatnonzero(dates == day)
        close, high, low, volume = (bars[column].to_numpy(float)[indexes] for column in ("close", "high", "low", "volume"))
        typical = (high + low + close) / 3
        cumulative_volume = np.cumsum(volume)
        values["vwap"][indexes] = np.divide(np.cumsum(typical * volume), cumulative_volume,
                                           out=np.full(len(indexes), np.nan), where=cumulative_volume > 0)
        if not continuous:
            values["fast"][indexes] = talib.EMA(close, fast_period)
            values["slow"][indexes] = talib.EMA(close, slow_period)
            values["atr"][indexes] = talib.ATR(high, low, close, 20)
    if continuous and len(bars):
        close, high, low = (bars[column].to_numpy(float) for column in ("close", "high", "low"))
        values["fast"] = talib.EMA(close, fast_period)
        values["slow"] = talib.EMA(close, slow_period)
        values["atr"] = talib.ATR(high, low, close, 20)
    if "sessionVwap" in bars:
        # Preserve the original minute-volume weighting when higher-period OHLC are synthesized.
        values["vwap"] = bars["sessionVwap"].to_numpy(float)
    return values


def decision_bars(bars: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Aggregate complete clock-aligned blocks without filling gaps or crossing lunch/dates."""
    if minutes == 1:
        result = bars.copy().reset_index(drop=True)
        result["availableAt"] = result["datetime"] + pd.Timedelta(1, unit="min")
        return result
    if minutes not in {5, 15}:
        raise ValueError("做T决策周期仅支持1、5、15分钟")
    raw = bars.copy().reset_index(drop=True)
    timestamps = raw["datetime"]
    minute_of_day = timestamps.dt.hour * 60 + timestamps.dt.minute
    morning, afternoon = minute_of_day.between(570, 689), minute_of_day.between(780, 899)
    origin = timestamps.dt.normalize() + pd.to_timedelta(np.where(morning, 570, 780), unit="min")
    offset = (timestamps - origin).dt.total_seconds() // (minutes * 60)
    raw["block"] = origin + pd.to_timedelta(offset * minutes, unit="min")
    typical = (raw["high"] + raw["low"] + raw["close"]) / 3
    dates = timestamps.dt.date
    volume = raw["volume"].groupby(dates).cumsum()
    raw["sessionVwap"] = (typical * raw["volume"]).groupby(dates).cumsum() / volume.replace(0, np.nan)
    records = []
    for start, group in raw.loc[morning | afternoon].groupby("block", sort=True):
        expected = pd.date_range(start, periods=minutes, freq="min")
        if len(group) != minutes or not pd.DatetimeIndex(group["datetime"]).equals(expected):
            continue
        records.append({"datetime": group["datetime"].iloc[-1], "availableAt": start + pd.Timedelta(minutes, unit="min"),
            "open": group["open"].iloc[0], "high": group["high"].max(), "low": group["low"].min(),
            "close": group["close"].iloc[-1], "volume": group["volume"].sum(), "sessionVwap": group["sessionVwap"].iloc[-1]})
    return pd.DataFrame(records, columns=["datetime", "availableAt", "open", "high", "low", "close", "volume", "sessionVwap"])


def base_t_condition(close: np.ndarray, indicators: dict[str, np.ndarray], current: int,
                     atr_multiplier: float, cost_rate: float) -> tuple[bool, bool, float]:
    """Observe premium exhaustion and pullback recovery; neither condition knows account holdings."""
    previous = current - 1
    if previous < 0 or not np.isfinite([indicators[name][index] for name in indicators for index in (previous, current)]).all():
        return False, False, 0.
    fast, slow, vwap, atr = (indicators[name] for name in ("fast", "slow", "vwap", "atr"))
    # A sampled-price ATR omits within-minute range: also require at least two ticks and the cost budget.
    distance = max(atr[current] * atr_multiplier, close[current] * 2 * cost_rate, 2 * PRICE_TICK)
    # Confirm a local trend rollover before selling a premium; a single pullback in a strong rally is insufficient.
    sell = bool(close[current] - vwap[current] >= distance
                and fast[current] < slow[current]
                and fast[previous] < slow[previous]
                and fast[current] < fast[previous]
                and close[current] < close[previous])
    recover = bool(close[current] <= vwap[current] and close[current] > close[previous])
    return sell, recover, float(distance)


@dataclass
class BaseTAccount:
    """Track actual cash and T+1 inventory rather than simulating unlimited same-day turnover."""

    cash: float
    shares: int
    sellable: int
    locked: int = 0
    pending: dict[str, Any] | None = None
    executions: list[dict[str, Any]] = field(default_factory=list)
    trades: list[dict[str, Any]] = field(default_factory=list)

    def execute(self, side: str, quantity: int, raw_price: float, cost_rate: float,
                timestamp: str, reason: str) -> dict[str, Any]:
        """Apply one lot-sized fill; bought shares stay locked until the following observed session."""
        if side not in {"sell", "buy"}:
            raise ValueError("成交方向必须为卖出或买回")
        execution_price = raw_price * (1 - cost_rate if side == "sell" else 1 + cost_rate)
        if quantity <= 0 or quantity % LOT_SIZE:
            raise ValueError("做T成交数量必须是正数且为100份的整数倍")
        if side == "sell":
            if quantity > self.sellable:
                raise ValueError("不能卖出超过当日可卖旧份额的数量")
            self.cash += quantity * execution_price
            self.shares -= quantity
            self.sellable -= quantity
        else:
            if quantity * execution_price > self.cash + 1e-8:
                raise ValueError("买回资金不足，不能透支现金")
            self.cash -= quantity * execution_price
            self.shares += quantity
            self.locked += quantity
        fill = {"time": timestamp, "side": side, "price": float(execution_price), "rawPrice": float(raw_price),
                "shares": quantity, "reason": reason, "transactionCost": float(quantity * raw_price * cost_rate),
                "sellableShares": self.sellable, "lockedShares": self.locked, "cash": float(self.cash)}
        self.executions.append(fill)
        return fill


def run_base_t_backtest(bars: pd.DataFrame, initial_capital: float, fee_rate: float, slippage_bps: float,
                        fast_period: int, slow_period: int, parameters: dict[str, Any], *,
                        include_cost_sensitivity: bool = True) -> dict[str, Any]:
    """Start with overnight inventory, sell old shares once each, and buy back without recycling locked units."""
    close, open_price = bars["close"].to_numpy(float), bars["open"].to_numpy(float)
    dates, timestamps = bars["datetime"].dt.date.to_numpy(), bars["datetime"].to_list()
    base_shares = int(initial_capital * parameters["basePositionPct"] / 100 / close[0] / LOT_SIZE) * LOT_SIZE
    tranche = int(base_shares * parameters["tPositionPct"] / 100 / LOT_SIZE) * LOT_SIZE
    if tranche < LOT_SIZE:
        raise ValueError("初始权益及底仓/单次做T比例不足100份；请增加资金或仓位比例")
    initial_cash = initial_capital - base_shares * close[0]
    account = BaseTAccount(float(initial_cash), base_shares, base_shares)
    cost_rate = fee_rate + slippage_bps / 10_000
    minutes = parameters["decisionMinutes"]
    decisions = decision_bars(bars, minutes)
    indicators = session_indicators(decisions, fast_period, slow_period, continuous=minutes > 1)
    decision_close = decisions["close"].to_numpy(float)
    available_at = pd.DatetimeIndex(decisions["availableAt"]).asi8
    decision_dates = pd.to_datetime(decisions["datetime"]).dt.date.to_numpy()
    equity, baseline, exposure = [], [], []
    previous_setup = False
    last_decision_index = -1
    for index, timestamp in enumerate(timestamps):
        if index and dates[index] != dates[index - 1]:
            # Only the next trading session unlocks bought units; lunch never resets the sell quota.
            account.sellable, account.locked = account.shares, 0
            previous_setup = False
        if index:
            signal_index = index - 1
            decision_index = int(np.searchsorted(available_at, timestamp.value, side="right")) - 1
            new_decision = decision_index >= 0 and decision_index != last_decision_index
            setup, recovery, distance = (
                base_t_condition(decision_close, indicators, decision_index, parameters["atrMultiplier"], cost_rate)
                if new_decision and decision_dates[decision_index] == dates[index] else (False, False, 0.)
            )
            new_setup = setup and not previous_setup
            if new_decision:
                previous_setup = setup
                last_decision_index = decision_index
            if account.pending:
                pending = account.pending
                held = decision_index - pending["decisionIndex"]
                # Sale-time ATR is frozen; a changing ATR must not retroactively move this pair's thresholds.
                target = pending["rawPrice"] - pending["distance"]
                rebound_stop = close[signal_index] >= pending["rawPrice"] + pending["distance"]
                restore_due = timestamp.time() >= RESTORE_TIME
                next_day = dates[index] != pending["date"]
                take_pullback = (new_decision and held >= parameters["minHoldBars"]
                                 and decision_close[decision_index] <= target and recovery)
                if restore_due or next_day or rebound_stop or take_pullback:
                    reason = "次日补回未完成底仓" if next_day else "14:55起恢复底仓" if restore_due else "上涨保护买回" if rebound_stop else "回落企稳买回"
                    affordable = int(max(0., account.cash) / (open_price[index] * (1 + cost_rate)) / LOT_SIZE) * LOT_SIZE
                    quantity = min(pending["shares"], affordable)
                    if quantity:
                        fill = account.execute("buy", quantity, open_price[index], cost_rate, timestamp.isoformat(), reason)
                        pnl = quantity * (pending["price"] - fill["price"])
                        account.trades.append({"entry": pending["time"], "exit": fill["time"],
                            "entryPrice": pending["price"], "exitPrice": fill["price"], "direction": "sell_buy",
                            "shares": quantity, "pnl": float(pnl), "returnPct": float(pnl / (quantity * pending["rawPrice"]) * 100),
                            "transactionCost": float(quantity * pending["rawPrice"] * cost_rate + fill["transactionCost"]),
                            "exitReason": reason})
                        pending["shares"] -= quantity
                        if not pending["shares"]:
                            account.pending = None
            elif new_setup and account.sellable >= tranche and time(9, 45) <= timestamp.time() < SELL_CUTOFF:
                # Do not chase an opening gap that has already erased the signal's premium.
                if open_price[index] - indicators["vwap"][decision_index] >= distance:
                    quantity = min(tranche, account.sellable)
                    fill = account.execute("sell", quantity, open_price[index], cost_rate, timestamp.isoformat(), "VWAP溢价回落，卖出旧底仓")
                    account.pending = {**fill, "decisionIndex": decision_index, "date": dates[index], "distance": distance}
        # A missing closing quote never creates an invented close fill. An unfinished T remains marked to market.
        equity.append({"time": timestamp.isoformat(), "value": float(account.cash + account.shares * close[index])})
        baseline.append({"time": timestamp.isoformat(), "value": float(initial_cash + base_shares * close[index])})
        exposure.append(account.shares * close[index] / equity[-1]["value"])
    curve = np.array([point["value"] for point in equity])
    benchmark = np.array([point["value"] for point in baseline])
    final_equity = float(curve[-1])
    pending = account.pending
    result = {"initialCapital": initial_capital, "finalEquity": final_equity,
        "totalReturnPct": float((final_equity / initial_capital - 1) * 100),
        "annualizedReturnPct": None, "sharpe": None,
        "maxDrawdownPct": float(np.min(curve / np.maximum.accumulate(curve) - 1) * 100),
        "winRatePct": float(np.mean([trade["pnl"] > 0 for trade in account.trades]) * 100) if account.trades else 0.,
        "tradeCount": len(account.trades), "exposurePct": float(np.mean(exposure) * 100),
        "openPosition": bool(account.shares), "openPositions": [], "equityCurve": equity,
        "trades": account.trades, "executions": account.executions, "strategyType": "intraday_scalp", "parameters": parameters,
        "benchmark": {"name": "同等初始底仓持有", "totalReturnPct": float((benchmark[-1] / initial_capital - 1) * 100),
                      "maxDrawdownPct": float(np.min(benchmark / np.maximum.accumulate(benchmark) - 1) * 100), "equityCurve": baseline},
        "baseT": {"decisionMinutes": minutes, "decisionBarCount": len(decisions),
                  "initialShares": base_shares, "initialCash": float(initial_cash), "currentShares": account.shares,
                  "sellableShares": account.sellable, "lockedShares": account.locked, "cash": float(account.cash),
                  "pendingBuybackShares": pending["shares"] if pending else 0,
                  "basePnl": float(benchmark[-1] - initial_capital), "tPnl": float(final_equity - benchmark[-1]),
                  "tReturnPct": float((final_equity - benchmark[-1]) / initial_capital * 100),
                  "closedTPnl": float(sum(trade["pnl"] for trade in account.trades)),
                  "transactionCost": float(sum(fill["transactionCost"] for fill in account.executions)),
                  "initialization": "期初假设已有前一交易日持有的底仓，按首个样本价估值；初始权益包含底仓市值与现金，未模拟建仓费用。"},
        "sampleWarning": f"当前样本{len(np.unique(dates))}个交易日（末日可能未收盘）。公开分时为分钟采样价，并非完整分钟OHLC；ATR/VWAP及下一分钟执行价均为近似值。短样本不能证明长期收益。",
        "caveat": "底仓做T采用先卖旧份额、再买回；买回份额次日才可卖出。底仓承担隔夜价格风险；14:55起尝试恢复底仓，缺报价或现金不足时保留待买回份额并盯市。未模拟最低佣金、盘口价差、涨跌停或市场冲击。"}
    if include_cost_sensitivity:
        result["costSensitivity"] = []
        for label, fee, slippage in (("零成本", 0., 0.), ("双倍成本", min(fee_rate * 2, .02), min(slippage_bps * 2, 500))):
            scenario = run_base_t_backtest(bars, initial_capital, fee, slippage, fast_period, slow_period, parameters, include_cost_sensitivity=False)
            result["costSensitivity"].append({"scenario": label, "totalReturnPct": scenario["totalReturnPct"],
                                               "maxDrawdownPct": scenario["maxDrawdownPct"], "tReturnPct": scenario["baseT"]["tReturnPct"]})
    return result


def analyze_base_t(bars: pd.DataFrame, fast_period: int, slow_period: int, parameters: dict[str, Any],
                   fee_rate: float, slippage_bps: float) -> dict[str, Any]:
    """Expose shared price conditions while explicitly withholding a position-aware trade instruction."""
    minutes = parameters["decisionMinutes"]
    decisions = decision_bars(bars, minutes)
    indicators = session_indicators(decisions, fast_period, slow_period, continuous=minutes > 1)
    current = len(decisions) - 1
    cost_rate = fee_rate + slippage_bps / 10_000
    sell, recovery, distance = base_t_condition(decisions["close"].to_numpy(float), indicators, current, parameters["atrMultiplier"], cost_rate)
    current_session = current >= 0 and decisions.iloc[-1]["datetime"].date() == bars.iloc[-1]["datetime"].date()
    ready = current_session and current > 0 and all(np.isfinite(values[current - 1:current + 1]).all() for values in indicators.values())
    in_window = time(9, 45) <= bars.iloc[-1]["datetime"].time() < SELL_CUTOFF
    sell = sell and in_window
    return {"entryCondition": recovery if ready else False, "exitCondition": sell if ready else False,
        "decisionMinutes": minutes, "decisionBarCount": len(decisions), "basis": f"{minutes}分钟K",
        "asOf": (decisions.iloc[-1]["availableAt"].isoformat() if minutes > 1 else decisions.iloc[-1]["datetime"].isoformat()) if current_session else None,
        "signal": "数据不足" if not ready else "旧底仓减仓观察条件" if sell else "回落企稳，待结合卖出价判断买回" if recovery else "等待底仓做T机会",
        "action": "observe", "tradingMode": "base_t", "costAssumption": {"feeRate": fee_rate, "slippageBps": slippage_bps},
        "message": (
            f"{minutes}分钟决策K线不足，或EMA{fast_period}/{slow_period}、当日VWAP与ATR未完成预热。" if not ready else
            f"当日VWAP {indicators['vwap'][current]:.4f}，溢价/价差门槛至少 {distance:.4f}；仅观察短趋势转弱后的溢价衰减与回落企稳。"
        ) + f" 基于已收盘{minutes}分钟K线确认；买回等待{parameters['minHoldBars']}根决策K，保护买回仍逐分钟检查。"
            f" 假设期初已有{parameters['basePositionPct']:g}%底仓，每次最多使用初始底仓的{parameters['tPositionPct']:g}%；当天买回份额不可再次卖出。"
            "无真实可卖份额和前次卖出价，不能判断实际卖出或买回；买回还需覆盖成本、满足等待条件，或触发上涨保护/14:55起定时恢复。"}
