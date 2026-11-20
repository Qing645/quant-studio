"""Canonical strategy template metadata shared by the API and research UI."""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any


def _channel_template(risk_template: dict[str, Any]) -> dict[str, Any]:
    """Reuse the ATR execution contract while giving channel periods their own rule identity."""
    defaults = {"initialAtrMultiplier": 3, "trailingAtrMultiplier": 2, "riskBudgetPct": 4,
                "maxPositionPct": 90, "gapAtrMultiplier": 2}
    definitions = [{**deepcopy(field), "default": defaults[field["key"]]}
                   for field in risk_template["parameterDefinitions"] if field["key"] in defaults]
    return {
        "strategyType": "star50_channel",
        "label": "科创50 ETF 趋势与量能确认通道 · 研究版",
        "templateName": "科创50 ETF 趋势与量能确认通道 · 研究版",
        "defaultSymbol": "588080", "defaultTimeframe": "daily",
        "defaultFastPeriod": 10, "defaultSlowPeriod": 15,
        "parameterDefinitions": [
            {"key": "trendFilterPeriod", "label": "趋势状态过滤均线", "default": 200, "min": 100,
             "max": 250, "step": 10, "integer": True, "unit": "根日K SMA",
             "help": "只允许收盘高于该均线时的新入场。"},
            {"key": "trendSlopeBars", "label": "均线走升确认间隔", "default": 20, "min": 20,
             "max": 100, "step": 10, "integer": True, "unit": "根日K",
             "help": "SMA趋势过滤线须高于此前此间隔时的数值；较短间隔更早确认趋势，也可能更早遇到失败突破。"},
            {"key": "volumeMultiplier", "label": "突破最低量能", "default": 1, "min": 0.5,
             "max": 2, "step": 0.1, "integer": False, "unit": "倍此前20日均量",
             "help": "只接受成交量不低于此前20根已完成日K平均量的突破，降低无量假突破。"},
            *definitions,
        ],
        "description": "588080趋势与量能确认后价格通道：收盘高于SMA200、SMA200高于20日前、突破此前15根高点且成交量不低于此前20根均量时入场；跌破此前10根低点或持仓ATR风险线退出。年化15%目标仍未达到。",
        "entryRule": "已完成日K收盘>SMA{trendFilterPeriod}，该均线>SMA{trendSlopeBars}日前数值，突破此前{slowPeriod}根最高价（排除本根）且成交量≥此前20根均量×{volumeMultiplier}，下一交易日开盘待核验",
        "exitRule": "已完成日K收盘<此前{fastPeriod}根最低价（排除本根）或持仓ATR风险线，下一可执行交易日开盘退出",
        "entryLabel": "趋势与量能确认突破", "exitLabel": "价格通道退出",
        "executionNote": "新入场须收盘高于SMA{trendFilterPeriod}且该均线高于{trendSlopeBars}日前水平，突破前{slowPeriod}日高点并放量至此前20根均量×{volumeMultiplier}；信号收盘−{initialAtrMultiplier}×ATR20冻结初始风险线；持仓最高收盘−{trailingAtrMultiplier}×ATR20且只上移。单次价格风险预算{riskBudgetPct}%，开仓上限{maxPositionPct}%，100份取整；开盘偏离超过{gapAtrMultiplier}×ATR20取消入场。T+1、下一开盘执行，不摊平、不加杠杆。研究目标年化15%、回撤约20%，不代表已达标。",
        "supportedSymbols": ["588080"], "supportedTimeframes": ["daily"],
        "scopeLabel": "588080 科创50 ETF · 日K · 上升趋势过滤通道",
        "scopeError": "科创50价格通道趋势仅支持588080日K",
        "caveat": "上升趋势过滤通道尚未达到全区间年化15%目标，收益集中于有限趋势交易，过滤线走升周期仍有历史选择风险。复权日K成交近似，未计最低佣金与真实盘口。T+1、跳空和不可成交可扩大亏损；约20%回撤是研究目标，不是损失上限。",
    }


STRATEGY_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "strategyType": "star50_pullback",
        "label": "科创50 ETF 顺势回撤与ATR风控 · 研究版",
        "templateName": "科创50 ETF 顺势回撤与ATR风控 · 研究版",
        "defaultSymbol": "588080", "defaultTimeframe": "daily",
        "defaultFastPeriod": 5, "defaultSlowPeriod": 200,
        "parameterDefinitions": [
            {"key": "rsiThreshold", "label": "RSI2超卖阈值", "default": 10, "min": 1, "max": 30, "step": 1, "integer": False, "unit": "RSI2", "help": "收盘RSI2不高于此值，且收盘仍高于长期趋势均线，才观察入场。"},
            {"key": "initialAtrMultiplier", "label": "初始风险距离", "default": 2.5, "min": 0.5, "max": 10, "step": 0.1, "integer": False, "unit": "倍 ATR20", "help": "信号收盘减此倍数ATR20，初始风险线在信号日冻结。"},
             {"key": "trailingAtrMultiplier", "label": "持仓跟踪距离", "default": 2, "min": 0.5, "max": 10, "step": 0.1, "integer": False, "unit": "倍 ATR20", "help": "持仓最高已完成收盘价减此倍数ATR20；收盘触发后下一交易日退出，风险线只上移。较窄距离会更早退出，也可能在震荡中反复卖出再入场。"},
            {"key": "riskBudgetPct", "label": "单次计划价格风险", "default": 2, "min": 0.05, "max": 5, "step": 0.05, "integer": False, "unit": "%策略资金", "help": "按开盘价至初始风险线距离计算份额；费用、跳空和T+1可使实际亏损超预算。"},
            {"key": "maxPositionPct", "label": "开仓市值上限", "default": 80, "min": 1, "max": 100, "step": 1, "integer": False, "unit": "%策略资金", "help": "这是开仓上限，不是固定仓位；不摊平、不自动加仓。"},
            {"key": "gapAtrMultiplier", "label": "开盘偏离取消阈值", "default": 1, "min": 0.1, "max": 5, "step": 0.1, "integer": False, "unit": "倍信号 ATR20", "help": "开盘相对信号收盘的绝对偏离超过阈值则取消；保护退出不受此限制。"},
        ],
        "description": "588080顺势回撤研究版：收盘高于SMA200且RSI2≤10时观察入场，恢复至SMA5或触及ATR风险线后退出。风险预算2%、开仓上限80%。已接入实时分析与回测，尚未达到年化15%的研究目标。",
        "entryRule": "已完成日K收盘>SMA{slowPeriod}且RSI2≤{rsiThreshold}，下一交易日开盘待核验",
        "exitRule": "已完成日K收盘≥SMA{fastPeriod}时观察退出；持仓另检查只上移的ATR风险线，下一可执行交易日开盘退出",
        "entryLabel": "顺势回撤条件", "exitLabel": "均值恢复条件",
        "executionNote": "信号收盘−{initialAtrMultiplier}×ATR20冻结初始风险线；持仓最高收盘−{trailingAtrMultiplier}×ATR20且只上移。风险预算{riskBudgetPct}%，开仓上限{maxPositionPct}%，100份取整；开盘偏离超过{gapAtrMultiplier}×ATR20取消入场。T+1、下一开盘执行，不摊平或自动加仓。研究目标年化15%、回撤约20%，不代表已达标。",
        "supportedSymbols": ["588080"], "supportedTimeframes": ["daily"],
        "scopeLabel": "588080 科创50 ETF · 日K · 顺势回撤研究版",
        "scopeError": "科创50顺势回撤研究版仅支持588080日K",
        "caveat": "研究版尚未达到年化15%目标；历史选择与参数敏感性仍存在。复权日K成交近似，缺少真实盘口、分红现金流与最低佣金。T+1、跳空和不可成交可扩大亏损，约20%回撤是研究目标而非损失上限。",
    },
    {
        "strategyType": "star50_breakout",
        "label": "科创50 ETF 趋势突破与ATR风控",
        "templateName": "科创50 ETF 趋势突破与ATR风控",
        "defaultSymbol": "588080",
        "defaultTimeframe": "daily",
        "defaultFastPeriod": 20,
        "defaultSlowPeriod": 60,
        "parameterDefinitions": [
            {"key": "slopeBars", "label": "趋势均线比较间隔", "default": 5, "min": 1, "max": 60, "step": 1, "integer": True, "unit": "根日K", "help": "趋势均线须高于此前该根数时的均线。"},
            {"key": "breakoutPeriod", "label": "突破回看周期", "default": 20, "min": 2, "max": 250, "step": 1, "integer": True, "unit": "根日K", "help": "最高价窗口排除信号K线，收盘须严格突破。"},
            {"key": "volumeMultiplier", "label": "突破放量倍数", "default": 1.2, "min": 0.5, "max": 5, "step": 0.1, "integer": False, "unit": "倍", "help": "成交量不低于此前20根均量的此倍数，均量排除信号K线。"},
            {"key": "initialAtrMultiplier", "label": "初始风险距离", "default": 2.5, "min": 0.5, "max": 10, "step": 0.1, "integer": False, "unit": "倍 ATR20", "help": "信号收盘价减去此倍数ATR20，初始风险线在信号日冻结。"},
            {"key": "trailingAtrMultiplier", "label": "持仓跟踪距离", "default": 3, "min": 0.5, "max": 10, "step": 0.1, "integer": False, "unit": "倍 ATR20", "help": "持仓最高已完成收盘价减去此倍数当前ATR20；风险线只抬高、不下移。"},
            {"key": "riskBudgetPct", "label": "单次计划价格风险", "default": 0.5, "min": 0.05, "max": 5, "step": 0.05, "integer": False, "unit": "%策略资金", "help": "按开盘价至初始风险线的距离计算份额；不是最大亏损保证，费用与跳空可超预算。"},
            {"key": "maxPositionPct", "label": "开仓市值上限", "default": 50, "min": 1, "max": 100, "step": 1, "integer": False, "unit": "%策略资金", "help": "这是开仓上限，不是固定仓位；不摊平、不自动加仓或每日再平衡。"},
            {"key": "gapAtrMultiplier", "label": "开盘偏离取消阈值", "default": 1, "min": 0.1, "max": 5, "step": 0.1, "integer": False, "unit": "倍信号 ATR20", "help": "开盘相对信号收盘价的绝对偏离超过阈值，则取消本次入场；不限制保护退出。"},
        ],
        "description": "588080日K趋势突破研究：站上且抬升的SMA60、突破前20根高点并放量时观察入场。按ATR风险距离和资金预算计算整数手；持仓风险线只上移，触线或连续两根跌至SMA20下方后次日退出。",
        "entryRule": "收盘>SMA{slowPeriod}且该均线高于前{slopeBars}根；收盘突破前{breakoutPeriod}根最高价，成交量≥此前20根均量×{volumeMultiplier}",
        "exitRule": "连续两根收盘≤各自SMA{fastPeriod}；持仓另检查只上移的ATR风险线，实时窗口不掌握持仓，不能判定该风险退出",
        "entryLabel": "趋势突破条件",
        "exitLabel": "均线退出条件",
        "executionNote": "已完成日K确认、下一交易日开盘尝试执行；初始风险线=信号收盘−{initialAtrMultiplier}×ATR20，跟踪线=持仓最高收盘−{trailingAtrMultiplier}×ATR20且只上移。单次价格风险预算{riskBudgetPct}%，开仓上限{maxPositionPct}%，100份取整；开盘偏离超过{gapAtrMultiplier}×信号ATR20取消入场。前复权日K回测为研究近似。",
        "supportedSymbols": ["588080"],
        "supportedTimeframes": ["daily"],
        "scopeLabel": "588080 科创50 ETF · 日K",
        "scopeError": "科创50趋势突破与ATR风控仅支持588080的日K",
        "caveat": "前复权日K研究近似：未映射未复权成交价、分红现金流和真实盘口，整数手为复权价格尺度下的模拟。下一可观测日K开盘假设可成交；零量、已知不可成交及涨跌停方向保守阻止成交，日K不能验证实际排队成交或缺失交易日。T+1与跳空可使损失超过风险预算；不连接交易账户，不保证收益。",
    },
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
STRATEGY_TEMPLATES = (_channel_template(STRATEGY_TEMPLATES[0]), *STRATEGY_TEMPLATES)


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
