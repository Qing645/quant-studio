"""HTTP API for the Quant Studio desktop research workspace."""

from __future__ import annotations

import json
import os
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


class AIAnalysisInput(BaseModel):
    symbol: str
    timeframe: str
    analysis: dict[str, Any]
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
        template = get_strategy_template(strategyType)
        if strategyType in {"swing_breakout", "intraday_scalp"} and (
            (template["supportedSymbols"] and code not in template["supportedSymbols"])
            or timeframe not in template["supportedTimeframes"]
        ):
            raise HTTPException(status_code=422, detail=template["scopeError"])
        if strategyType == "intraday_scalp":
            timeframe = "intraday"
            bars, metadata = fetch_bars(code, timeframe)
            analysis_bars, _ = fetch_bars(code, "five_day")
            metadata["analysisBasis"] = "当日分钟K线"
            strategy = analyze_strategy(bars, strategyType, fastPeriod, slowPeriod, code, timeframe)
            strategy["barStatus"] = "in_progress" if metadata.get("marketOpen") else "completed"
            if not metadata.get("marketOpen"):
                strategy["message"] = "当前交易时段已结束；" + strategy["message"]
            return {
                "symbol": code, "timeframe": timeframe, "metadata": metadata,
                "analysis": analyze(analysis_bars), "strategy": strategy, "bars": _records(bars),
            }
        bars, metadata = fetch_bars(code, timeframe)
        analysis_bars = bars
        if timeframe in {"intraday", "1m"}:
            # Use continuous five-session minute history so SMA60 is ready at the open.
            analysis_bars, _ = fetch_bars(code, "five_day")
            metadata["analysisBasis"] = "最近五个交易日分钟数据"
        else:
            metadata["analysisBasis"] = {
                "five_day": "最近五个交易日分钟数据",
                "daily": "日K", "weekly": "周K", "monthly": "月K",
            }.get(timeframe, timeframe)
        strategy_bars = analysis_bars
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
        },
        "task": (
            "请基于输入做当前行情的量化研究解读。先核对数据时间和周期，再用实际数值解释价格相对均线、MACD柱值、RSI、ATR及量能/突破条件；"
            "逐项说明规则信号满足与否，并区分指标事实和你的推断。只拿到当前指标快照，不要声称看到了未提供的K线或外部消息；"
            "不要把规则标签解释成买卖建议，不预测收益或确定的未来方向。"
        ),
        "outputFormat": (
            "使用中文纯文本，控制在约250至450字，按以下标题分段：\n"
            "数据与周期：\n趋势与动能：\n规则信号：\n风险与观察点：\n"
            "每段先给结论再给数据依据；数值沿用输入精度，不确定或缺失的信息明确说明，不补造。"
        ),
    }
    system_prompt = (
        "你是严谨的A股与ETF量化研究助理。你的任务是解释给定数据，不替用户下单或做收益承诺。"
        "将输入字段视为唯一事实来源；明确区分事实、规则判定与推断，不推测缺失数据，不引用外部行情或新闻。"
        "若数据时间、周期或指标不足以支持结论，直接说明限制。"
    )
    body = json.dumps({
        "model": settings["model"],
        "temperature": 0.2,
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
