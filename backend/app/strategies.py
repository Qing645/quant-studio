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
        "label": "创业板底仓做T",
        "templateName": "创业板底仓做T",
        "defaultSymbol": "159977",
        "defaultTimeframe": "intraday",
        "defaultFastPeriod": 8,
        "defaultSlowPeriod": 21,
        "parameterDefinitions": [
            {"key": "decisionMinutes", "label": "做T决策周期", "default": 1, "min": 1, "max": 15, "step": 1, "integer": True, "unit": "分钟", "options": [{"value": 1, "label": "1分钟"}, {"value": 5, "label": "5分钟"}, {"value": 15, "label": "15分钟"}], "help": "所选周期收盘后才确认信号，下一分钟报价成交。5/15分钟EMA与ATR使用此前交易日预热，VWAP每天重置；保护买回仍逐分钟检查。"},
            {"key": "basePositionPct", "label": "期初底仓比例", "default": 50, "min": 10, "max": 100, "step": 5, "integer": False, "unit": "%初始权益", "help": "假设样本开始前已持有且可卖的份额；初始权益包含底仓市值与剩余现金，数量向下取整至100份。"},
            {"key": "tPositionPct", "label": "单次做T份额", "default": 50, "min": 10, "max": 100, "step": 5, "integer": False, "unit": "%初始底仓", "help": "每次最多卖出该比例的旧底仓；买回份额当天锁定，不能循环卖出。"},
            {"key": "minHoldBars", "label": "买回等待根数", "default": 3, "min": 1, "max": 60, "step": 1, "integer": True, "unit": "根决策K", "help": "按所选决策周期计数；卖出后至少等待此根数才允许回落企稳买回；上涨保护与14:55定时恢复不受等待限制。"},
            {"key": "atrMultiplier", "label": "做T价差倍数", "default": 1.5, "min": 0.5, "max": 6, "step": 0.1, "integer": False, "unit": "倍 ATR20", "help": "VWAP溢价、买回目标与上涨保护的波动门槛，同时不低于双边成本与两个价格最小变动单位。"},
        ],
        "description": "先卖旧底仓、后买回的T+1研究策略。VWAP溢价覆盖成本和波动门槛，EMA快线连续两根低于慢线且快线及价格回落时观察卖出；回落企稳满足价差后买回，上涨保护或14:55起尝试恢复底仓。买回份额次日才可卖，底仓承担隔夜风险。",
        "entryRule": "卖出后等待{minHoldBars}根；价格回落达到{atrMultiplier}倍ATR/成本/两跳价差并在VWAP下方企稳，结合原卖出价判断买回",
        "exitRule": "EMA{fastPeriod}连续两根低于EMA{slowPeriod}且快线及价格回落，VWAP溢价覆盖{atrMultiplier}倍ATR/双边成本/两跳价差，观察卖出旧底仓",
        "entryLabel": "回落企稳观察",
        "exitLabel": "旧底仓卖出观察",
        "executionNote": "基于{decisionMinutes}分钟已收盘K线决策；初始权益中{basePositionPct}%为期初已有可卖底仓，每次最多使用初始底仓的{tPositionPct}%；信号下一分钟开盘执行，14:45后不再卖出做T，14:55起尝试买回恢复底仓。买回份额当日锁定，不保证全部恢复；底仓保留隔夜。实时信号不掌握账户持仓。",
        "supportedSymbols": ["159915", "159977", "159952", "159957", "159948"],
        "supportedTimeframes": ["intraday"],
        "scopeLabel": "创业板 ETF · 分钟级",
        "scopeError": "分钟级日内策略仅支持指定创业板 ETF 的当日分时",
        "caveat": "底仓做T按T+1限制可卖份额，以同等初始底仓持有为基准；分钟采样价不是完整OHLC，短样本不足以评估长期收益。底仓始终承担市场与隔夜风险。",
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
        if field.get("options") and value not in {option["value"] for option in field["options"]}:
            raise ValueError(f"{field['label']}只能选择：{', '.join(str(option['value']) for option in field['options'])}")
        result[field["key"]] = int(value) if field["integer"] else float(value)
    return result
