"""HTTP API for the Quant Studio desktop research workspace."""

from __future__ import annotations

import json
import math
import os
from statistics import fmean, pstdev
import subprocess
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import keyring
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from http.client import HTTPException as ClientHTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from .engine import analyze_strategy, run_backtest
from .market import analyze, fetch_bars, fetch_daily_history, normalize_symbol
from .strategies import get_strategy_template, get_strategy_templates


APP_ROOT = Path(__file__).resolve().parents[2]
STRATEGIES_FILE = APP_ROOT / "data" / "strategies.json"
AI_SERVICE = "quant-studio-ai"
AI_USER_AGENT = "QuantStudio/0.1.0"
SUPPORTED_TIMEFRAMES = {"intraday", "five_day", "daily", "weekly", "monthly"}
app = FastAPI(title="Quant Studio API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_origin_regex=r"^https?://(127\.0\.0\.1|localhost):\d+$",
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["Content-Type"],
)


class StrategyInput(BaseModel):
    id: str | None = None
    name: str = Field(min_length=1, max_length=60)
    strategyType: Literal["trend_filter", "swing_breakout", "intraday_scalp"] = "trend_filter"
    fastPeriod: int = Field(default_factory=lambda: get_strategy_template("trend_filter")["defaultFastPeriod"], ge=2, le=100)
    slowPeriod: int = Field(default_factory=lambda: get_strategy_template("trend_filter")["defaultSlowPeriod"], ge=3, le=250)
    description: str = Field(default="", max_length=500)


class BacktestInput(BaseModel):
    symbol: str = "159977"
    timeframe: Literal["intraday", "five_day", "daily", "weekly", "monthly"] = "daily"
    strategyType: Literal["trend_filter", "swing_breakout", "intraday_scalp"] = "trend_filter"
    initialCapital: float = Field(default=100_000, gt=0, le=100_000_000)
    feeRate: float = Field(default=0.0003, ge=0, le=0.02)
    slippageBps: float = Field(default=5, ge=0, le=500)
    fastPeriod: int = Field(default_factory=lambda: get_strategy_template("trend_filter")["defaultFastPeriod"], ge=2, le=100)
    slowPeriod: int = Field(default_factory=lambda: get_strategy_template("trend_filter")["defaultSlowPeriod"], ge=3, le=250)


class AISettings(BaseModel):
    baseUrl: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=120)
    apiKey: str = Field(default="", max_length=1000)

    @field_validator("baseUrl")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        """Require encrypted transport except for a loopback model service."""
        parsed = urlsplit(value)
        local_hosts = {"localhost", "127.0.0.1", "::1"}
        if not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError("模型地址格式无效")
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in local_hosts):
            raise ValueError("模型服务必须使用 HTTPS；仅本机服务允许 HTTP")
        return value


class AIBar(BaseModel):
    datetime: str
    open: float
    high: float
    low: float
    close: float
    volume: float = Field(ge=0)


class AIAnalysisInput(BaseModel):
    symbol: str
    timeframe: str
    analysis: dict[str, Any]
    strategy: dict[str, Any] | None = None
    bars: list[AIBar] = Field(default_factory=list, max_length=500)
    sourceTime: str
    analysisBasis: str = ""


@app.get("/api/health")
def health() -> dict[str, str]:
    """Report backend readiness for the local workspace UI."""
    return {"status": "ok", "service": "Quant Studio", "mode": "research-only"}


@app.get("/api/market")
def market(
    symbol: str = "159977",
    timeframe: Literal["intraday", "five_day", "daily", "weekly", "monthly"] = "daily",
    strategyType: Annotated[Literal["trend_filter", "swing_breakout", "intraday_scalp"], Query()] = "trend_filter",
    fastPeriod: Annotated[int, Query(ge=2, le=100)] = 20,
    slowPeriod: Annotated[int, Query(ge=3, le=250)] = 60,
) -> dict[str, Any]:
    """Return public bars, technical indicators and the selected strategy signal."""
    try:
        if timeframe not in SUPPORTED_TIMEFRAMES:
            raise HTTPException(status_code=422, detail="不支持的行情周期")
        if fastPeriod >= slowPeriod:
            raise HTTPException(status_code=422, detail="快线周期必须小于慢线周期")
        code = normalize_symbol(symbol)
        bars, metadata = fetch_bars(code, timeframe)
        analysis_bars = bars
        if timeframe in {"intraday", "1m"}:
            # Use continuous five-session minute history so SMA60 is ready at the open.
            analysis_bars, _ = fetch_bars(code, "five_day")
            metadata["analysisBasis"] = (
                "当日分钟K线"
                if strategyType == "intraday_scalp"
                else "最近五个交易日分钟数据"
            )
        else:
            metadata["analysisBasis"] = {
                "five_day": "最近五个交易日分钟数据",
                "daily": "日K", "weekly": "周K", "monthly": "月K",
            }.get(timeframe, timeframe)
        strategy_bars = bars if strategyType == "intraday_scalp" else analysis_bars
        strategy_bar_status = "completed"
        if (
            timeframe in {"daily", "weekly", "monthly"}
            and metadata.get("marketOpen")
            and len(analysis_bars) > 1
        ):
            # Keep the live candle for display, but confirm signals on the last closed candle.
            strategy_bars = analysis_bars.iloc[:-1].reset_index(drop=True)
            strategy_bar_status = "in_progress"
        strategy = analyze_strategy(
            strategy_bars, strategyType, fastPeriod, slowPeriod, code, timeframe,
        )
        strategy["barStatus"] = strategy_bar_status
        if strategy_bar_status == "in_progress" and strategy.get("supported"):
            strategy["message"] = (
                f"当前{timeframe}K线尚未收盘；正式信号基于 {strategy['asOf'][:10]} 的已完成K线。"
                f" {strategy['message']}"
            )
        return {
            "symbol": code,
            "timeframe": timeframe,
            "metadata": metadata,
            "analysis": analyze(analysis_bars),
            "strategy": strategy,
            "bars": _records(bars),
        }
    except HTTPException:
        raise
    except (ValueError, RuntimeError, OSError, KeyError, ClientHTTPException) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/strategies")
def get_strategies() -> list[dict[str, Any]]:
    """Load local strategy definitions, creating an empty store on first use."""
    STRATEGIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not STRATEGIES_FILE.exists():
        STRATEGIES_FILE.write_text("[]", encoding="utf-8")
    items = json.loads(STRATEGIES_FILE.read_text(encoding="utf-8"))
    return items


@app.get("/api/strategy-templates")
def list_strategy_templates() -> list[dict[str, Any]]:
    """Expose canonical strategy labels, defaults and display rules to the UI."""
    return get_strategy_templates()


@app.post("/api/strategies")
def save_strategy(strategy: StrategyInput) -> dict[str, Any]:
    """Create a versioned research strategy in the local project store."""
    if strategy.fastPeriod >= strategy.slowPeriod:
        raise HTTPException(status_code=422, detail="快线周期必须小于慢线周期")
    items = get_strategies()
    item = strategy.model_dump()
    item["id"] = strategy.id or os.urandom(8).hex()
    item["version"] = 1 + max((entry.get("version", 0) for entry in items if entry.get("id") == item["id"]), default=0)
    item["updatedAt"] = pd.Timestamp.now().isoformat(timespec="seconds")
    items = [entry for entry in items if entry.get("id") != item["id"]]
    items.insert(0, item)
    STRATEGIES_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    return item


@app.post("/api/backtest")
def backtest(request: BacktestInput) -> dict[str, Any]:
    """Run the selected strategy against available historical public bars."""
    try:
        if request.fastPeriod >= request.slowPeriod:
            raise HTTPException(status_code=422, detail="快线周期必须小于慢线周期")
        code = normalize_symbol(request.symbol)
        template = get_strategy_template(request.strategyType)
        supported_symbols = template["supportedSymbols"]
        supported_timeframes = template["supportedTimeframes"]
        if (supported_symbols and code not in supported_symbols) or request.timeframe not in supported_timeframes:
            raise HTTPException(status_code=422, detail=template["scopeError"])
        if request.strategyType in {"trend_filter", "swing_breakout"}:
            bars, metadata = fetch_daily_history(code)
        elif request.strategyType == "intraday_scalp":
            if request.timeframe != "intraday":
                raise HTTPException(status_code=422, detail="分钟级日内策略仅支持当日分时")
            bars, metadata = fetch_bars(code, "intraday")
        else:
            bars, metadata = fetch_bars(code, request.timeframe)
        result = run_backtest(
            bars, request.initialCapital, request.feeRate, request.slippageBps,
            request.fastPeriod, request.slowPeriod, request.strategyType,
        )
        result["bars"] = _records(bars)
        return {"symbol": code, "timeframe": request.timeframe, "metadata": metadata, **result}
    except HTTPException:
        raise
    except (RuntimeError, OSError, KeyError, ClientHTTPException) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/ai/settings")
def get_ai_settings() -> dict[str, Any]:
    """Return non-secret model settings and whether a key is available."""
    config_file = APP_ROOT / "data" / "ai-settings.json"
    settings = json.loads(config_file.read_text(encoding="utf-8")) if config_file.exists() else {}
    try:
        has_key = bool(keyring.get_password(AI_SERVICE, "default"))
    except Exception:
        has_key = bool(os.getenv("QUANT_STUDIO_AI_KEY"))
    return {**settings, "hasApiKey": has_key}


@app.put("/api/ai/settings")
def save_ai_settings(settings: AISettings) -> dict[str, Any]:
    """Store model metadata locally and keep the API key in system keychain."""
    config_file = APP_ROOT / "data" / "ai-settings.json"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        if settings.apiKey:
            keyring.set_password(AI_SERVICE, "default", settings.apiKey)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"无法写入系统钥匙串：{exc}") from exc
    safe_settings = {"baseUrl": settings.baseUrl.rstrip("/"), "model": settings.model}
    config_file.write_text(json.dumps(safe_settings, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**safe_settings, "hasApiKey": bool(settings.apiKey) or get_ai_settings()["hasApiKey"]}


@app.post("/api/ai/analyze")
def analyze_with_ai(request: AIAnalysisInput) -> dict[str, str]:
    """Ask a configured OpenAI-compatible model to explain supplied signals."""
    settings = get_ai_settings()
    if not settings.get("baseUrl") or not settings.get("model"):
        raise HTTPException(status_code=409, detail="请先配置模型服务地址和模型名称")
    try:
        api_key = keyring.get_password(AI_SERVICE, "default") or os.getenv("QUANT_STUDIO_AI_KEY", "")
    except Exception:
        api_key = os.getenv("QUANT_STUDIO_AI_KEY", "")
    if not api_key:
        raise HTTPException(status_code=409, detail="请先保存 API Key")
    endpoint = _chat_endpoint(settings["baseUrl"])
    prompt = {
        "symbol": request.symbol,
        "timeframe": {
            "intraday": "分时", "1m": "分时", "five_day": "五日",
            "daily": "日K", "weekly": "周K", "monthly": "月K",
        }.get(request.timeframe, request.timeframe),
        "sourceTime": request.sourceTime,
        "analysisBasis": request.analysisBasis,
        "indicatorsAndSignals": request.analysis,
        "selectedStrategy": request.strategy,
        "historicalContext": _build_quant_context(request.bars),
        "fieldDefinitions": {
            "changePct": "最新一根K线收盘价相对前一根K线收盘价的涨跌幅百分比。",
            "macd": "TA-Lib MACD 柱值，不是 MACD 线；正值只表示该指标当前为正。",
            "rsi14": "14周期 RSI，范围通常为0到100；仅作动能参考，不单独构成信号。",
            "atr20": "20周期 ATR，单位与价格相同，用于描述波动幅度，不是目标价。",
            "trend": "最新收盘价是否高于 SMA60。",
            "momentum": "MACD 柱值是否大于0。",
            "volumeExpansion": "近5根K线平均成交量是否超过近30根均量的1.25倍。",
            "breakout": "最新收盘价是否高于此前70根K线的最高价。",
            "signal": "仅当 trend、momentum 均为真，且 volumeExpansion 或 breakout 至少一项为真时满足。",
            "returnsPctByBars": "按5、20、60根当前所选周期K线计算的收盘价收益；样本不足时为null。",
            "recentRangeByBars.positionPct": "最新收盘价在对应窗口最高价与最低价之间的位置，0接近区间低点，100接近区间高点；不是支撑阻力位。",
            "recentRangeByBars.drawdownFromHighPct": "最新收盘价相对窗口内最高价的百分比变化，负值表示从高点回撤。",
            "sma20ChangeVsPrior20Pct": "最近20根收盘均价相对其前一段20根收盘均价的百分比变化。",
            "realizedVolatilityPct20": "最近20个单根K线收益率标准差乘以根数平方根的波动估算，不是年化值也不表示涨跌方向。",
            "recentMoveSizeVsPrior15": "最近5根单根绝对收益均值相对之前15根的比值；大于1表示近期单根波动幅度扩大。",
        },
        "task": (
            "请像一位熟悉量化研究的分析师，针对这一次数据写一段有重点的中文解读。先独立检查历史上下文，比较短、中、"
            "长窗口的表现、价格在近期区间的位置、回撤和波动变化，找出至少一条不能从策略标签本身直接读出的观察。"
            "不要只重述策略方向或列指标，要解释行情处于什么阶段、相对近端走势是延续还是变化。判断哪些证据互相印证、"
            "哪些相互冲突，再解释这对当前行情状态意味着什么。结合所选策略的 signal、action、"
            "entryCondition、exitCondition 说明规则为什么处于当前状态；若策略信号与短期反弹、动能或其他指标不一致，"
            "解释它们观察尺度不同，不要把矛盾抹平。最后给出两三个可验证的观察条件，并说明什么变化会加强或削弱当前判断。"
            "历史上下文中的收益窗口按K线根数计算，不能自行换算成自然日。区间位置不是支撑阻力预测，波动率也不代表方向。"
            "避免固定套用标题、重复免责声明、泛泛而谈和机械罗列指标；"
            "不要给买卖指令、仓位建议、目标价、收益预测或确定性方向判断。"
        ),
        "responseStyle": (
            "中文自然表达，通常两到四个短段即可，长短由行情复杂度决定；可以直接使用小标题，但不要每次套用同一组标题。"
            "引用输入中的具体数值作为依据，区分数据事实、策略规则和分析判断。历史上下文由当前请求所带K线计算，"
            "窗口用K线根数标示；不要把它改述成天数。没有可用的历史上下文时，不得假称做过多周期比较。"
            "不要声称看过未提供的盘中路径、新闻或资金流；缺失字段就略过或说明无法判断。"
            "不要为了显得全面重复所有指标，也不要照抄策略说明。"
        ),
    }
    system_prompt = (
        "你是严谨但表达自然的A股与ETF量化研究助理。先判断这次数据真正重要的变化或矛盾，再围绕它解释，"
        "不要把每份回答写成相同的指标清单。将输入字段视为唯一事实来源；明确区分行情事实、策略规则判定和你的推断。"
        "策略 signal/action 是规则输出，不等于投资建议。数据时间、周期或指标不足时简洁说明具体限制，不推测缺失数据，"
        "不引用外部行情或新闻，不承诺收益或预测确定方向。"
    )
    body = json.dumps({
        "model": settings["model"],
        "temperature": 0.55,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
    }).encode("utf-8")
    req = Request(endpoint, data=body, headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": AI_USER_AGENT,
    })
    try:
        with urlopen(req, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return {"analysis": payload["choices"][0]["message"]["content"]}
    except HTTPError as exc:
        raise HTTPException(status_code=502, detail=_format_upstream_error(exc, api_key)) from exc
    except (URLError, TimeoutError, KeyError, IndexError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail=f"模型请求失败：{exc}") from exc


@app.post("/api/ai/test")
def test_ai_connection() -> dict[str, str]:
    """Check configured API credentials with a low-token model request."""
    settings = get_ai_settings()
    if not settings.get("baseUrl") or not settings.get("model") or not settings.get("hasApiKey"):
        raise HTTPException(status_code=409, detail="请先填写服务地址、模型名称和 API Key")
    try:
        api_key = keyring.get_password(AI_SERVICE, "default") or os.getenv("QUANT_STUDIO_AI_KEY", "")
    except Exception:
        api_key = os.getenv("QUANT_STUDIO_AI_KEY", "")
    endpoint = _chat_endpoint(settings["baseUrl"])
    body = json.dumps({"model": settings["model"], "max_tokens": 16, "messages": [{"role": "user", "content": "只回复 OK"}]}).encode()
    req = Request(endpoint, data=body, headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": AI_USER_AGENT,
    })
    try:
        with urlopen(req, timeout=20) as response:
            response.read()
        return {"status": "connected"}
    except HTTPError as exc:
        raise HTTPException(status_code=502, detail=_format_upstream_error(exc, api_key)) from exc
    except (URLError, TimeoutError) as exc:
        raise HTTPException(status_code=502, detail=f"模型连接失败：{exc}") from exc


def _records(bars: pd.DataFrame) -> list[dict[str, Any]]:
    """Convert pandas values to JSON-safe chart records."""
    records = bars.copy()
    records["datetime"] = records["datetime"].dt.strftime("%Y-%m-%d %H:%M:%S")
    return records.to_dict(orient="records")


def _build_quant_context(bars: list[AIBar]) -> dict[str, Any]:
    """Summarize multi-horizon structure and risk from the supplied bars."""
    valid = [
        bar for bar in bars
        if all(math.isfinite(value) for value in (bar.open, bar.high, bar.low, bar.close, bar.volume))
        and min(bar.open, bar.high, bar.low, bar.close) > 0
        and bar.high >= max(bar.open, bar.close)
        and bar.low <= min(bar.open, bar.close)
    ]
    if len(valid) < 2:
        return {"barCount": len(valid), "availability": "至少需要两根有效K线"}

    closes = [bar.close for bar in valid]
    highs = [bar.high for bar in valid]
    lows = [bar.low for bar in valid]
    latest = closes[-1]
    returns: dict[str, float | None] = {}
    for window in (5, 20, 60):
        returns[str(window)] = round((latest / closes[-window - 1] - 1) * 100, 2) if len(closes) > window else None

    ranges: dict[str, dict[str, float | None]] = {}
    for window in (20, 60):
        if len(closes) < window:
            ranges[str(window)] = {"positionPct": None, "drawdownFromHighPct": None}
            continue
        range_high = max(highs[-window:])
        range_low = min(lows[-window:])
        ranges[str(window)] = {
            "positionPct": round((latest - range_low) / (range_high - range_low) * 100, 1) if range_high > range_low else 50.0,
            "drawdownFromHighPct": round((latest / range_high - 1) * 100, 2),
        }

    closes20 = closes[-20:]
    context: dict[str, Any] = {
        "barCount": len(valid),
        "firstBar": valid[0].datetime,
        "lastBar": valid[-1].datetime,
        "returnsPctByBars": returns,
        "recentRangeByBars": ranges,
        "sma20": round(fmean(closes20), 4) if len(closes) >= 20 else None,
        "sma60": round(fmean(closes[-60:]), 4) if len(closes) >= 60 else None,
    }
    if len(closes) >= 40:
        prior_sma20 = fmean(closes[-40:-20])
        context["sma20ChangeVsPrior20Pct"] = round((fmean(closes20) / prior_sma20 - 1) * 100, 2) if prior_sma20 else None
    bar_returns = [closes[index] / closes[index - 1] - 1 for index in range(1, len(closes))]
    if len(bar_returns) >= 20:
        context["realizedVolatilityPct20"] = round(pstdev(bar_returns[-20:]) * math.sqrt(20) * 100, 2)
        recent = fmean(abs(value) for value in bar_returns[-5:])
        baseline = fmean(abs(value) for value in bar_returns[-20:-5])
        context["recentMoveSizeVsPrior15"] = round(recent / baseline, 2) if baseline else None
    else:
        context["realizedVolatilityPct20"] = None
        context["recentMoveSizeVsPrior15"] = None
    return context


def _chat_endpoint(base_url: str) -> str:
    """Normalize an OpenAI-compatible base URL to its chat endpoint."""
    endpoint = base_url.rstrip("/")
    return endpoint if endpoint.endswith("/chat/completions") else endpoint + "/chat/completions"


def _format_upstream_error(error: HTTPError, api_key: str) -> str:
    """Show a short upstream rejection reason while redacting the configured key."""
    raw_body = error.read(4096).decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        payload = {}

    detail: Any = payload.get("error", {}) if isinstance(payload, dict) else {}
    if isinstance(detail, dict):
        detail = detail.get("message") or detail.get("code")
    if not detail and isinstance(payload, dict):
        detail = payload.get("message") or payload.get("detail")
    message = str(detail or raw_body or error.reason).strip()
    if api_key:
        message = message.replace(api_key, "[redacted]")
    message = " ".join(message.split())[:240]
    return f"模型服务返回 HTTP {error.code}: {message or error.reason}"
