"""Validated user-authored rules and a causal evaluator shared by live research and backtests."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

import numpy as np
import pandas as pd
import talib
from pydantic import BaseModel, ConfigDict, Field, model_validator


INDICATORS = (
    {"value": "close", "label": "收盘价", "period": False},
    {"value": "open", "label": "开盘价", "period": False},
    {"value": "high", "label": "最高价", "period": False},
    {"value": "low", "label": "最低价", "period": False},
    {"value": "volume", "label": "成交量", "period": False},
    {"value": "sma", "label": "SMA均线", "period": True},
    {"value": "ema", "label": "EMA均线", "period": True},
    {"value": "rsi", "label": "RSI", "period": True},
    {"value": "atr", "label": "ATR", "period": True},
    {"value": "highest", "label": "区间最高价", "period": True},
    {"value": "lowest", "label": "区间最低价", "period": True},
    {"value": "volume_sma", "label": "成交量均值", "period": True},
    {"value": "number", "label": "固定数值", "period": False},
)
OPERATORS = (
    {"value": "gt", "label": "大于"}, {"value": "gte", "label": "大于等于"},
    {"value": "lt", "label": "小于"}, {"value": "lte", "label": "小于等于"},
    {"value": "crosses_above", "label": "向上穿越"}, {"value": "crosses_below", "label": "向下穿越"},
)
PERIOD_INDICATORS = {item["value"] for item in INDICATORS if item["period"]}
TIMEFRAMES = (
    {"value": "intraday", "label": "分钟K"}, {"value": "daily", "label": "日K"},
    {"value": "weekly", "label": "周K"}, {"value": "monthly", "label": "月K"},
)


class RuleModel(BaseModel):
    """Reject unsupported fields and nonfinite numeric settings at the contract boundary."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


class Operand(RuleModel):
    """A causal price/indicator reference or constant, with an optional historical offset."""

    kind: Literal["close", "open", "high", "low", "volume", "sma", "ema", "rsi", "atr", "highest", "lowest", "volume_sma", "number"]
    period: int | None = Field(default=None, ge=2, le=250)
    value: float | None = Field(default=None, ge=-1e12, le=1e12)
    offset: int = Field(default=0, ge=0, le=250)
    multiplier: float = Field(default=1, gt=0, le=1000)

    @model_validator(mode="after")
    def validate_reference(self) -> Operand:
        """Require only the fields meaningful for the selected indicator or constant."""
        if self.kind in PERIOD_INDICATORS and self.period is None:
            raise ValueError("所选指标必须设置周期")
        if self.kind not in PERIOD_INDICATORS and self.period is not None:
            raise ValueError("该数据项不使用指标周期")
        if (self.kind == "number") != (self.value is not None):
            raise ValueError("固定数值必须填写数值，指标不能填写固定数值")
        if self.kind == "number" and self.offset:
            raise ValueError("固定数值不能引用历史K线")
        return self


class Condition(RuleModel):
    """One comparison, including crossing events that require a previous completed bar."""

    left: Operand
    operator: Literal["gt", "gte", "lt", "lte", "crosses_above", "crosses_below"]
    right: Operand


class ConditionGroup(RuleModel):
    """A nonempty bounded group composed with all/any semantics."""

    match: Literal["all", "any"] = "all"
    conditions: list[Condition] = Field(min_length=1, max_length=12)


class CustomRules(RuleModel):
    """The complete executable definition of an independent long-only research strategy."""

    symbol: str = Field(pattern=r"^\d{6}$")
    timeframe: Literal["intraday", "daily", "weekly", "monthly"]
    entry: ConditionGroup
    exit: ConditionGroup
    positionPct: float = Field(default=100, gt=0, le=100)
    minHoldBars: int = Field(default=1, ge=1, le=250)
    stopLossPct: float = Field(default=0, ge=0, le=50)
    takeProfitPct: float = Field(default=0, ge=0, le=200)
    trailingStopPct: float = Field(default=0, ge=0, le=50)
    closeAtSessionEnd: bool = False

    @model_validator(mode="after")
    def validate_session_policy(self) -> CustomRules:
        """Allow session flattening only for minute rules, whose bars expose session boundaries."""
        if self.closeAtSessionEnd and self.timeframe != "intraday":
            raise ValueError("仅分钟K策略可设置日终平仓")
        return self


def get_rule_options() -> dict[str, Any]:
    """Publish the editable vocabulary without mixing user rules with built-in strategies."""
    return deepcopy({"label": "自定义规则", "indicators": list(INDICATORS), "operators": list(OPERATORS),
                     "timeframes": list(TIMEFRAMES), "maxConditions": 12})


def normalize_custom_rules(rules: CustomRules | dict[str, Any] | None) -> CustomRules:
    """Validate API or direct-engine definitions through the same strict schema."""
    return rules if isinstance(rules, CustomRules) else CustomRules.model_validate(rules)



def completed_custom_bars(
    bars: pd.DataFrame, timeframe: str, *, now: pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, bool]:
    """Exclude the current unfinished candle, including a week/month still open after today's close.

    Calendar business month end is conservative around exchange holidays: a candle is not
    confirmed early merely because the public feed stopped updating before calendar period end.
    """
    if bars.empty:
        return bars, False
    current = now if now is not None else pd.Timestamp.now(tz="Asia/Shanghai")
    current = current.tz_localize("Asia/Shanghai") if current.tzinfo is None else current.tz_convert("Asia/Shanghai")
    last = pd.Timestamp(bars.iloc[-1]["datetime"])
    last = last.tz_localize("Asia/Shanghai") if last.tzinfo is None else last.tz_convert("Asia/Shanghai")
    if timeframe == "intraday":
        completion = last + pd.Timedelta(1, unit="min")
    elif timeframe == "weekly":
        completion = last.normalize() + pd.Timedelta((4 - last.weekday()) * 24 + 15, unit="h")
    elif timeframe == "monthly":
        completion = last.normalize() + pd.offsets.BMonthEnd(0) + pd.Timedelta(15, unit="h")
    else:
        completion = last.normalize() + pd.Timedelta(15, unit="h")
    in_progress = current < completion
    return (bars.iloc[:-1].reset_index(drop=True) if in_progress else bars), in_progress

def describe_operand(operand: Operand) -> str:
    """Format a reference with explicit offsets so breakout windows are unambiguous."""
    label = next(item["label"] for item in INDICATORS if item["value"] == operand.kind)
    text = f"{operand.value:g}" if operand.kind == "number" else label
    if operand.period is not None:
        text += f"({operand.period})"
    if operand.offset:
        text += f"[前{operand.offset}根]"
    if operand.multiplier != 1:
        text += f" × {operand.multiplier:g}"
    return text


def describe_group(group: ConditionGroup) -> str:
    """Describe exactly the comparisons used by the shared evaluator."""
    labels = {item["value"]: item["label"] for item in OPERATORS}
    return (" 且 " if group.match == "all" else " 或 ").join(
        f"{describe_operand(item.left)} {labels[item.operator]} {describe_operand(item.right)}"
        for item in group.conditions
    )


class RuleEvaluator:
    """Compile only trailing indicators once; every output at N depends on bars at or before N."""

    def __init__(self, bars: pd.DataFrame, rules: CustomRules) -> None:
        """Cache indicator arrays and compile entry/exit masks with warmup readiness."""
        self.bars = bars
        self.rules = rules
        self.cache: dict[tuple, np.ndarray] = {}
        self.entry, self.entry_ready = self.group(rules.entry)
        self.exit, self.exit_ready = self.group(rules.exit)

    def operand(self, operand: Operand) -> np.ndarray:
        """Resolve an operand with nonnegative lag and no centered or forward windows."""
        key = (operand.kind, operand.period, operand.value, operand.offset, operand.multiplier)
        if key in self.cache:
            return self.cache[key]
        close = self.bars["close"].to_numpy(dtype=float)
        kind, period = operand.kind, operand.period
        if kind == "number":
            values = np.full(len(close), operand.value, dtype=float)
        elif kind in {"open", "high", "low", "close", "volume"}:
            values = self.bars[kind].to_numpy(dtype=float)
        elif kind in {"sma", "ema", "rsi"}:
            values = getattr(talib, kind.upper())(close, timeperiod=period)
        elif kind == "atr":
            values = talib.ATR(self.bars["high"].to_numpy(float), self.bars["low"].to_numpy(float), close, timeperiod=period)
        elif kind == "volume_sma":
            values = self.bars["volume"].rolling(period, min_periods=period).mean().to_numpy()
        elif kind == "highest":
            values = self.bars["high"].rolling(period, min_periods=period).max().to_numpy()
        else:
            values = self.bars["low"].rolling(period, min_periods=period).min().to_numpy()
        values = pd.Series(values).shift(operand.offset).to_numpy() * operand.multiplier
        self.cache[key] = values
        return values

    def group(self, group: ConditionGroup) -> tuple[np.ndarray, np.ndarray]:
        """Wait for all operands to warm up, then evaluate the selected group logic."""
        matches, readiness = [], []
        for condition in group.conditions:
            left, right = self.operand(condition.left), self.operand(condition.right)
            ready = np.isfinite(left) & np.isfinite(right)
            operator = condition.operator
            if operator == "gt":
                matched = left > right
            elif operator == "gte":
                matched = left >= right
            elif operator == "lt":
                matched = left < right
            elif operator == "lte":
                matched = left <= right
            else:
                prior_left, prior_right = np.roll(left, 1), np.roll(right, 1)
                ready &= np.isfinite(prior_left) & np.isfinite(prior_right)
                ready[0] = False
                matched = ((left > right) & (prior_left <= prior_right) if operator == "crosses_above"
                           else (left < right) & (prior_left >= prior_right))
            matches.append(matched & ready)
            readiness.append(ready)
        ready = np.logical_and.reduce(readiness)
        combined = np.logical_and.reduce(matches) if group.match == "all" else np.logical_or.reduce(matches)
        return combined & ready, ready

    def live_signal(self) -> dict[str, Any]:
        """Report indicator conditions without pretending to know the user's actual holdings."""
        ready = bool(self.entry_ready[-1] and self.exit_ready[-1])
        entry, exit_ = bool(self.entry[-1]), bool(self.exit[-1])
        return {
            "supported": True, "strategyType": "custom", "rules": self.rules.model_dump(exclude_none=True),
            "asOf": self.bars.iloc[-1]["datetime"].isoformat(), "price": float(self.bars.iloc[-1]["close"]),
            "basis": next(item["label"] for item in TIMEFRAMES if item["value"] == self.rules.timeframe),
            "entryCondition": entry if ready else False, "exitCondition": exit_ if ready else False, "action": "observe",
            "signal": ("入场与离场条件同时满足" if entry and exit_ else "自定义入场条件满足" if entry
                       else "自定义离场条件满足" if exit_ else "等待自定义条件") if ready else "数据不足",
            "message": ((f"入场：{describe_group(self.rules.entry)}。离场：{describe_group(self.rules.exit)}。"
                         f"回测目标仓位{self.rules.positionPct:g}%；持有至少{self.rules.minHoldBars}根后允许条件退出，"
                         f"止损{self.rules.stopLossPct:g}%、止盈{self.rules.takeProfitPct:g}%、跟踪回撤{self.rules.trailingStopPct:g}%（0为关闭），"
                         "风险退出不受最短持有期限制。当前无实际持仓成本和持有时间，仅展示条件；回测在下一根开盘执行。"
                         + ("启用日终平仓，完整交易日末按收盘价平仓。" if self.rules.closeAtSessionEnd else ""))
                        if ready else "当前样本不足以预热全部入场与离场指标及历史偏移，暂不生成正式信号。"),
        }
