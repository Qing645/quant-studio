"""Canonical strategy template metadata shared by the API and research UI."""

from __future__ import annotations

from copy import deepcopy
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
        "description": "独立日K波段策略：收盘突破前20日高点、成交量超过近20日均量1.2倍，且收盘位于SMA60上方时入场；连续两根收盘低于快线或较近20日收盘高点回撤2倍ATR20时离场。信号下一根开盘执行，使用长期复权日线样本。",
        "entryRule": "收盘突破前{slowPeriod}根高点、成交量放大且价格在SMA60上方",
        "exitRule": "连续两根收盘低于SMA{fastPeriod}，或较近20日高点回撤2倍ATR20",
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
        "description": "独立分钟级日内策略：EMA8/21趋势向上且价格连续两根分钟K站在当日VWAP上方时入场；最短持有3根分钟K，之后连续两根跌破VWAP、EMA死叉确认或回撤达到1.5倍ATR20时退出，样本结束强制平仓，不隔夜。公开源仅提供当日分钟样本，结果不构成长周期验证。",
        "entryRule": "EMA{fastPeriod} 高于 EMA{slowPeriod} 且连续两根收盘站在当日 VWAP 上方",
        "exitRule": "最短持有3根；连续两根跌破 VWAP、EMA死叉确认或回撤达到1.5倍 ATR20；收盘前平仓",
        "entryLabel": "日内入场",
        "exitLabel": "日内退出",
        "executionNote": "只用已完成分钟K线判断，下一分钟开盘执行；持仓至少3根分钟K后允许信号退出，当日样本结束强制平仓，不持仓过夜。",
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
        "description": "适用于创业板 ETF 日线：SMA200 用于显示牛熊背景；实际进出场采用 SMA60 中期趋势，连续两根收盘站上时入场，连续两根收盘低于 SMA60 时离场。低位显著反弹但未站稳中期趋势时仅标记为观察。开仓及每月首个交易日按此前20日年化波动率调整仓位，目标估算值20%，最高不超过全部资金。",
        "entryRule": "连续两根收盘站上 SMA{slowPeriod} 时入场；按近20日年化波动率估算仓位",
        "exitRule": "连续两根收盘低于 SMA{slowPeriod} 时离场；SMA200 显示长期牛熊背景",
        "entryLabel": "持有条件",
        "exitLabel": "离场条件",
        "executionNote": "正式信号只使用已完成日K；SMA200 为市场背景，不阻止中期趋势修复入场；仓位目标年化波动率20%，上限100%，下一根开盘执行并计入成本。",
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
