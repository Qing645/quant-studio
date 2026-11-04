"""Canonical strategy template metadata shared by the API and research UI."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


STRATEGY_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "strategyType": "growth_momentum",
        "label": "创业板 ETF 趋势动量",
        "templateName": "159977 创业板 ETF 趋势动量",
        "defaultSymbol": "159977",
        "defaultTimeframe": "daily",
        "defaultFastPeriod": 20,
        "defaultSlowPeriod": 60,
        "description": "日线研究模板：SMA20 高于 SMA60 且收盘价站上 SMA60，MACD 柱由负转正并且 RSI14 位于 50–75 时入场；收盘价跌破 SMA20、MACD 柱转负或 RSI14 低于 45 时离场。信号在下一根K线开盘执行。",
        "entryRule": "SMA{fastPeriod} 高于 SMA{slowPeriod}、收盘价站上慢线、MACD 柱上穿零轴、RSI14 位于 50–75",
        "exitRule": "收盘价跌破 SMA{fastPeriod}、MACD 柱下穿零轴或 RSI14 低于 45",
        "entryLabel": "开仓",
        "exitLabel": "离场",
        "executionNote": "信号在下一根K线开盘执行；回测采用全仓模型，仅用于研究。",
        "supportedSymbols": ["159977"],
        "supportedTimeframes": ["daily"],
        "scopeLabel": "适用标的 159977 · 日K",
        "scopeError": "创业板ETF趋势动量模板仅适用于 159977 日K",
        "entryRsiMin": 50,
        "entryRsiMax": 75,
        "exitRsiBelow": 45,
        "caveat": "159977创业板ETF趋势动量研究规则；固定全仓、未计波动率仓位控制，数据受公开源限制，未建模涨跌停、分红税和市场冲击。",
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
        "entryRsiMin": None,
        "entryRsiMax": None,
        "exitRsiBelow": None,
        "caveat": "创业板 ETF 趋势风控研究策略；月度波动率调整会增加换手和成本，不能消除跳空、急跌或趋势反转风险，样本结果不保证未来收益。",
    },
    {
        "strategyType": "ml_direction",
        "label": "机器学习方向概率",
        "templateName": "多标的日线机器学习研究",
        "defaultSymbol": "159977",
        "defaultTimeframe": "daily",
        "defaultFastPeriod": 20,
        "defaultSlowPeriod": 60,
        "description": "实验性日线模型：使用价格收益、均线偏离、MACD、RSI、ATR和成交量特征，滚动训练逻辑回归估计未来5根日K上涨概率；概率达到55%入场，低于45%离场。历史样本外结果接近随机时，不应仅靠调整阈值追求更高收益。",
        "entryRule": "样本外上涨概率达到 55% 时入场",
        "exitRule": "样本外上涨概率低于 45% 时离场",
        "entryLabel": "开仓",
        "exitLabel": "离场",
        "executionNote": "每20根日K重新训练；只用已完成的未来5日标签训练，信号在下一根K线开盘执行。",
        "supportedSymbols": [],
        "supportedTimeframes": ["daily"],
        "scopeLabel": "适用于支持的股票或 ETF 日K组合",
        "scopeError": "机器学习方向概率策略仅支持日K",
        "entryRsiMin": None,
        "entryRsiMax": None,
        "exitRsiBelow": None,
        "caveat": "机器学习方向概率仅用于研究；各标的等分初始资金，未来5日标签重叠，未建模涨跌停、分红税和市场冲击。",
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
