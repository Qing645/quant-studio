"""Canonical strategy template metadata shared by the API and research UI."""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any


STRATEGY_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "strategyType": "swing_breakout",
        "label": "创业板日K短线波段",
        "templateName": "创业板日K短线波段",
        "defaultSymbol": "159977",
        "defaultTimeframe": "daily",
        "defaultFastPeriod": 10,
        "defaultSlowPeriod": 20,
        "parameterDefinitions": [
            {"key": "volumeMultiplier", "label": "突破放量倍数", "default": 1.2, "min": 0.5, "max": 5, "step": 0.1, "integer": False, "unit": "倍", "help": "成交量须超过此前20根均量的此倍数，越高越严格。"},
            {"key": "atrMultiplier", "label": "ATR回撤倍数", "default": 2, "min": 0.5, "max": 6, "step": 0.1, "integer": False, "unit": "倍 ATR20", "help": "相对最近21根收盘高点的回撤阈值，越大越宽松。"},
        ],
        "description": "独立日K波段策略：突破此前高点、放量且站上SMA60时入场；连续两根跌破快线或达到ATR回撤阈值时离场。突破周期、退出均线、放量倍数和ATR倍数可配置，以规则预览为准；信号下一根开盘执行。",
        "entryRule": "收盘突破前{slowPeriod}根高点、成交量超过此前20根均量{volumeMultiplier}倍且价格在SMA60上方",
        "exitRule": "连续两根收盘低于SMA{fastPeriod}，或较最近21根收盘高点回撤{atrMultiplier}倍ATR20",
        "entryLabel": "波段入场",
        "exitLabel": "风险退出",
        "executionNote": "只使用已完成日K判断，下一根开盘成交；样本外仍可能出现跳空和急跌。",
        "supportedSymbols": ["159915", "159977", "159952", "159957", "159948"],
        "supportedTimeframes": ["daily"],
        "scopeLabel": "创业板 ETF · 日K",
        "scopeError": "创业板日K短线波段仅支持指定创业板 ETF 的日K",
        "caveat": "日K波段研究规则；快线破位需两根收盘确认，ATR风险退出仍可能因跳空扩大损失；突破信号次日才执行，历史表现不保证未来收益。",
    },
    {
        "strategyType": "intraday_scalp",
        "label": "分钟级日内交易",
        "templateName": "分钟级日内交易",
        "defaultSymbol": "159977",
        "defaultTimeframe": "intraday",
        "defaultFastPeriod": 8,
        "defaultSlowPeriod": 21,
        "parameterDefinitions": [
            {"key": "minHoldBars", "label": "最短持有根数", "default": 3, "min": 1, "max": 60, "step": 1, "integer": True, "unit": "根分钟K", "help": "达到此持有时长后才执行指标或ATR退出；完整交易日结束仍强制平仓。"},
            {"key": "atrMultiplier", "label": "ATR回撤倍数", "default": 1.5, "min": 0.5, "max": 6, "step": 0.1, "integer": False, "unit": "倍 ATR20", "help": "相对持仓后收盘高点的回撤阈值；实时窗口无持仓信息时只展示指标观察条件。"},
        ],
        "description": "独立分钟级日内策略：EMA趋势向上且连续两根站上当日VWAP时入场；满足最短持有时长后，连续两根跌破VWAP、EMA死叉确认或ATR回撤退出。均线周期、持有根数和ATR倍数可配置。完整交易日结束强制平仓；盘中保留未平仓并按最新价估值。当日样本不能替代长期验证。",
        "entryRule": "EMA{fastPeriod} 高于 EMA{slowPeriod} 且快线向上，连续两根收盘站在当日 VWAP 上方",
        "exitRule": "最短持有{minHoldBars}根；连续两根跌破 VWAP、EMA死叉确认或回撤达到{atrMultiplier}倍 ATR20；收盘前平仓",
        "entryLabel": "日内入场",
        "exitLabel": "离场观察条件",
        "executionNote": "只用已完成分钟K线判断，下一分钟开盘执行；持仓至少{minHoldBars}根分钟K后允许信号或{atrMultiplier}倍ATR退出，完整交易日样本结束强制平仓，不持仓过夜。盘中分析只展示指标条件，不代表实际持仓退出指令。",
        "supportedSymbols": ["159915", "159977", "159952", "159957", "159948"],
        "supportedTimeframes": ["intraday"],
        "scopeLabel": "创业板 ETF · 分钟级",
        "scopeError": "分钟级日内策略仅支持指定创业板 ETF 的当日分时",
        "caveat": "分钟级公开行情仅有当日样本，不足以评估策略的长期收益、回撤和夏普；需接入多年历史分钟数据后才能开展可靠统计回测。",
    },
    {
        "strategyType": "trend_filter",
        "label": "创业板 ETF 趋势风控",
        "templateName": "创业板 ETF 趋势风控",
        "defaultSymbol": "159977",
        "defaultTimeframe": "daily",
        "defaultFastPeriod": 20,
        "defaultSlowPeriod": 60,
        "parameterDefinitions": [
            {"key": "targetVolatilityPct", "label": "目标年化波动率", "default": 20, "min": 5, "max": 60, "step": 1, "integer": False, "unit": "%", "help": "开仓及月初按此前20日波动率估算仓位，上限100%；不是收益目标或风险保证。"},
        ],
        "description": "适用于创业板 ETF 日线：SMA200 显示牛熊背景；实际进出场以所配置中期均线为准，连续两根站上时入场，连续两根低于或等于均线时离场。开仓及每月首个交易日按此前20日年化波动率和配置的风险目标调整仓位，最高100%；目标不是收益保证。",
        "entryRule": "连续两根收盘站上 SMA{slowPeriod} 时入场；按近20日年化波动率和{targetVolatilityPct}%目标估算仓位",
        "exitRule": "连续两根收盘低于或等于 SMA{slowPeriod} 时离场；SMA200 显示长期牛熊背景",
        "entryLabel": "持有条件",
        "exitLabel": "离场条件",
        "executionNote": "正式信号只使用已完成日K；SMA200 为市场背景，不阻止中期趋势修复入场；仓位目标年化波动率{targetVolatilityPct}%，上限100%，下一根开盘执行并计入成本。",
        "supportedSymbols": ["159915", "159977", "159952", "159957", "159948"],
        "supportedTimeframes": ["daily"],
        "scopeLabel": "创业板 ETF · 日K",
        "scopeError": "创业板 ETF 趋势风控仅支持 159915、159977、159952、159957、159948 的日K",
        "caveat": "创业板 ETF 趋势风控研究策略；月度波动率调整会增加换手和成本，不能消除跳空、急跌或趋势反转风险，样本结果不保证未来收益。",
    },
)


def get_strategy_templates() -> list[dict[str, Any]]:
    """Return detached strategy template objects for API serialization."""
    return deepcopy(list(STRATEGY_TEMPLATES))


def get_strategy_template(strategy_type: str) -> dict[str, Any]:
    """Find a strategy template by its stable identifier."""
    for template in STRATEGY_TEMPLATES:
        if template["strategyType"] == strategy_type:
            return deepcopy(template)
    raise ValueError("不支持的策略类型")


def normalize_strategy_parameters(strategy_type: str, parameters: dict[str, Any] | None = None) -> dict[str, float | int]:
    """Validate type-specific numeric rules and fill defaults for older saved instances."""
    definitions = get_strategy_template(strategy_type)["parameterDefinitions"]
    if parameters is None:
        parameters = {}
    if not isinstance(parameters, dict):
        raise ValueError("策略参数必须为对象")
    unknown = parameters.keys() - {field["key"] for field in definitions}
    if unknown:
        raise ValueError(f"当前策略不支持参数：{', '.join(sorted(unknown))}")
    result = {}
    for field in definitions:
        value = parameters.get(field["key"], field["default"])
        if isinstance(value, bool) or not isinstance(value, (int, float)) or (isinstance(value, float) and not math.isfinite(value)):
            raise ValueError(f"{field['label']}必须为有限数值")
        if not field["min"] <= value <= field["max"]:
            raise ValueError(f"{field['label']}须在 {field['min']} 到 {field['max']} 之间")
        if field["integer"] and value != int(value):
            raise ValueError(f"{field['label']}必须为整数")
        result[field["key"]] = int(value) if field["integer"] else float(value)
    return result
