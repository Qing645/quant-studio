"""HTTP API for the Quant Studio desktop research workspace."""

from __future__ import annotations

import json
import math
import os
from statistics import fmean, pstdev
import subprocess
from datetime import datetime
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
from pydantic import BaseModel, Field, field_validator, model_validator

from .ai_analysis import build_research_messages, parse_research_response, research_request_options
from .ai_context import CHINA_TZ, fetch_market_context, reference_levels
from .custom_rules import CustomRules, completed_custom_bars, get_rule_options, normalize_custom_rules
from .engine import analyze_strategy, run_backtest
from .market import analyze, fetch_bars, fetch_daily_history, normalize_symbol
from .strategies import get_strategy_template, get_strategy_templates, normalize_strategy_parameters


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


class StrategyRules(BaseModel):
    """Validate independent user rules or the existing built-in configuration contract."""

    strategyType: Literal["trend_filter", "swing_breakout", "intraday_scalp", "star50_breakout", "star50_pullback", "star50_channel", "custom"] = "trend_filter"
    fastPeriod: int | None = Field(default=None, ge=2, le=100)
    slowPeriod: int | None = Field(default=None, ge=3, le=250)
    parameters: dict[str, Any] = Field(default_factory=dict)
    rules: CustomRules | None = None

    @model_validator(mode="after")
    def validate_rules(self) -> StrategyRules:
        """Keep rule definitions separate from the legacy algorithm-specific parameter fields."""
        if self.strategyType == "custom":
            if self.rules is None:
                raise ValueError("自定义策略必须填写入场、离场和风控规则")
            if self.fastPeriod is not None or self.slowPeriod is not None or self.parameters:
                raise ValueError("自定义策略使用独立规则，不能混入内置策略参数")
        else:
            if self.rules is not None:
                raise ValueError("内置策略不能混入自定义规则")
            template = get_strategy_template(self.strategyType)
            self.fastPeriod = self.fastPeriod if self.fastPeriod is not None else template["defaultFastPeriod"]
            self.slowPeriod = self.slowPeriod if self.slowPeriod is not None else template["defaultSlowPeriod"]
            if self.fastPeriod >= self.slowPeriod:
                raise ValueError("快线周期必须小于慢线周期")
            self.parameters = normalize_strategy_parameters(self.strategyType, self.parameters)
        return self


class StrategyInput(StrategyRules):
    """An independently versioned strategy instance with user-authored metadata."""

    id: str | None = None
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=500)


class BacktestInput(StrategyRules):
    """Historical execution settings combined with the selected strategy rules."""

    symbol: str = "159977"
    timeframe: Literal["intraday", "five_day", "daily", "weekly", "monthly"] = "daily"
    initialCapital: float = Field(default=100_000, gt=0, le=100_000_000)
    feeRate: float = Field(default=0.0003, ge=0, le=0.02)
    slippageBps: float = Field(default=5, ge=0, le=500)


class AISettings(BaseModel):
    baseUrl: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=120)
    apiKey: str = Field(default="", max_length=1000)

    @field_validator("model", mode="before")
    @classmethod
    def validate_model_name(cls, value: Any) -> Any:
        """Require an explicit provider model ID and normalize surrounding whitespace."""
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("请填写模型名称（服务商提供的模型 ID）")
        return value

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
    strategyType: Annotated[Literal["trend_filter", "swing_breakout", "intraday_scalp", "star50_breakout", "star50_pullback", "star50_channel", "custom"], Query()] = "trend_filter",
    fastPeriod: Annotated[int | None, Query(ge=2, le=100)] = None,
    slowPeriod: Annotated[int | None, Query(ge=3, le=250)] = None,
    parameters: Annotated[str, Query(max_length=2000)] = "{}",
    rules: Annotated[str | None, Query(max_length=20000)] = None,
    feeRate: Annotated[float, Query(ge=0, le=.02)] = .0003,
    slippageBps: Annotated[float, Query(ge=0, le=500)] = 5,
) -> dict[str, Any]:
    """Return public bars, technical indicators and the selected strategy signal."""
    try:
        if timeframe not in SUPPORTED_TIMEFRAMES:
            raise HTTPException(status_code=422, detail="不支持的行情周期")
        try:
            decoded_parameters = json.loads(parameters)
            if not isinstance(decoded_parameters, dict):
                raise ValueError("策略参数必须为对象")
            settings = StrategyRules.model_validate({
                "strategyType": strategyType, "fastPeriod": fastPeriod, "slowPeriod": slowPeriod,
                "parameters": decoded_parameters, "rules": json.loads(rules) if rules is not None else None,
            })
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        code = normalize_symbol(symbol)
        bars, metadata = fetch_bars(code, timeframe)
        analysis_bars = bars
        if timeframe in {"intraday", "1m"}:
            # Use continuous five-session minute history so SMA60 is ready at the open.
            analysis_bars, _ = fetch_bars(code, "five_day")
            metadata["analysisBasis"] = (
                "当日分钟K线"
                if strategyType == "intraday_scalp" or (settings.rules and settings.rules.timeframe == "intraday")
                else "最近五个交易日分钟数据"
            )
        else:
            metadata["analysisBasis"] = {
                "five_day": "最近五个交易日分钟数据",
                "daily": "日K", "weekly": "周K", "monthly": "月K",
            }.get(timeframe, timeframe)
        strategy_bars = bars if strategyType == "intraday_scalp" or (settings.rules and settings.rules.timeframe == "intraday") else analysis_bars
        if strategyType == "intraday_scalp" and timeframe == "intraday" and settings.parameters["decisionMinutes"] > 1:
            strategy_bars = analysis_bars
            metadata["analysisBasis"] = f"{settings.parameters['decisionMinutes']}分钟决策K · 最近五日分钟数据预热"
        strategy_bar_status = "completed"
        if settings.rules or strategyType in {"star50_breakout", "star50_pullback", "star50_channel"}:
            strategy_bars, in_progress = completed_custom_bars(strategy_bars, timeframe)
            strategy_bar_status = "in_progress" if in_progress else "completed"
        elif strategyType == "intraday_scalp" and timeframe == "intraday":
            # Intraday price sampling still has an unfinished last minute; do not turn it into a confirmed signal.
            strategy_bars, in_progress = completed_custom_bars(strategy_bars, "intraday")
            strategy_bar_status = "in_progress" if in_progress else "completed"
        elif (
            timeframe in {"daily", "weekly", "monthly"}
            and metadata.get("marketOpen")
            and len(strategy_bars) > 1
        ):
            # Keep the live candle for display, but confirm signals on the last closed candle.
            strategy_bars = strategy_bars.iloc[:-1].reset_index(drop=True)
            strategy_bar_status = "in_progress"
        strategy = analyze_strategy(
            strategy_bars, strategyType, settings.fastPeriod, settings.slowPeriod, code, timeframe,
            parameters=settings.parameters, rules=settings.rules,
            fee_rate=feeRate, slippage_bps=slippageBps,
        )
        strategy["barStatus"] = strategy_bar_status
        if strategy_bar_status == "in_progress" and strategy.get("supported"):
            strategy["message"] = (
                f"当前{timeframe}K线尚未收盘；正式信号基于 {(strategy['asOf'] or '未知时间')[:16]} 的已完成K线。"
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


def _read_strategy_store() -> list[dict[str, Any]]:
    """Read raw saved records so editing one instance leaves other definitions intact."""
    STRATEGIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not STRATEGIES_FILE.exists():
        STRATEGIES_FILE.write_text("[]", encoding="utf-8")
    return json.loads(STRATEGIES_FILE.read_text(encoding="utf-8"))


@app.get("/api/strategies")
def get_strategies() -> list[dict[str, Any]]:
    """Fill legacy defaults in responses without rewriting saved user definitions."""
    return [
        _strategy_response(item)
        for item in _read_strategy_store()
    ]


def _strategy_response(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize one saved instance and replace obsolete built-in day-flat copy without a data migration."""
    if item["strategyType"] == "custom":
        return {**item, "rules": normalize_custom_rules(item.get("rules")).model_dump(exclude_none=True)}
    result = {**item, "parameters": normalize_strategy_parameters(item["strategyType"], item.get("parameters"))}
    if item["strategyType"] == "intraday_scalp":
        template = get_strategy_template("intraday_scalp")
        result["description"] = template["description"]
        if result.get("name") in {"创业板分钟级日内交易", "分钟级日内交易"}:
            result["name"] = template["templateName"]
    return result


@app.get("/api/strategy-templates")
def list_strategy_templates() -> list[dict[str, Any]]:
    """Expose canonical strategy labels, defaults and display rules to the UI."""
    return get_strategy_templates()


@app.get("/api/strategy-rule-options")
def list_strategy_rule_options() -> dict[str, Any]:
    """Describe the independent rule editor's supported indicators and comparisons."""
    return get_rule_options()


@app.post("/api/strategies")
def save_strategy(strategy: StrategyInput) -> dict[str, Any]:
    """Create a versioned research strategy in the local project store."""
    items = _read_strategy_store()
    item = strategy.model_dump(exclude_none=True, exclude=(
        {"fastPeriod", "slowPeriod", "parameters"} if strategy.strategyType == "custom" else {"rules"}
    ))
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
        code = normalize_symbol(request.symbol)
        if request.rules is not None:
            if code != request.rules.symbol or request.timeframe != request.rules.timeframe:
                raise HTTPException(status_code=422, detail="回测标的和周期必须与自定义规则一致")
        else:
            template = get_strategy_template(request.strategyType)
            if code not in template["supportedSymbols"] or request.timeframe not in template["supportedTimeframes"]:
                raise HTTPException(status_code=422, detail=template["scopeError"])
        if request.strategyType in {"trend_filter", "swing_breakout", "star50_breakout", "star50_pullback", "star50_channel"} or (request.rules and request.timeframe == "daily"):
            bars, metadata = fetch_daily_history(code)
        elif request.strategyType == "intraday_scalp":
            if request.timeframe != "intraday":
                raise HTTPException(status_code=422, detail="分钟级日内策略仅支持当日分时")
            # Reuse the available dated five-session feed; the inventory ledger settles bought units per date.
            bars, metadata = fetch_bars(code, "five_day")
            bars, _ = completed_custom_bars(bars, "intraday")
            metadata["backtestBasis"] = "最近可用分钟样本 · T+1底仓做T"
        else:
            bars, metadata = fetch_bars(code, request.timeframe)
        result = run_backtest(
            bars, request.initialCapital, request.feeRate, request.slippageBps,
            request.fastPeriod, request.slowPeriod, request.strategyType,
            parameters=request.parameters, rules=request.rules,
            session_complete=(
                (request.strategyType == "intraday_scalp" or (request.rules and request.rules.timeframe == "intraday"))
                and metadata.get("sessionComplete", False)
            ),
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
def analyze_with_ai(request: AIAnalysisInput) -> dict[str, Any]:
    """Combine dated public evidence and the current strategy into one research report."""
    # Validate nested free-form evidence before any credential reads or network requests.
    try:
        json.dumps(request.model_dump(), allow_nan=False)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="AI分析数据不能包含非有限数值") from exc
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
    market_context = fetch_market_context()
    target = {
        "symbol": request.symbol, "timeframe": request.timeframe,
        "sourceTime": request.sourceTime, "analysisBasis": request.analysisBasis,
        "indicatorsAndSignals": request.analysis, "selectedStrategy": request.strategy,
        "historicalContext": _build_quant_context(request.bars),
    }
    body = json.dumps({
        "model": settings["model"], **research_request_options(settings["model"]),
        "messages": build_research_messages(target, market_context),
    }, ensure_ascii=False, allow_nan=False).encode("utf-8")
    req = Request(endpoint, data=body, headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": AI_USER_AGENT,
    })
    try:
        with urlopen(req, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))
        report = parse_research_response(payload)
        return {
            **report,
            "generatedAt": datetime.now(CHINA_TZ).isoformat(timespec="seconds"),
            "marketContextAsOf": market_context["retrievedAt"],
            "targetSourceTime": request.sourceTime,
            "sources": market_context["sources"], "warnings": market_context["warnings"],
        }
    except HTTPError as exc:
        raise HTTPException(status_code=502, detail=_format_upstream_error(exc, api_key)) from exc
    except (URLError, TimeoutError, KeyError, IndexError, TypeError, ValueError, ClientHTTPException) as exc:
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
        "referenceLevels": reference_levels([
            {"date": bar.datetime, "open": bar.open, "close": bar.close, "high": bar.high, "low": bar.low}
            for bar in valid
        ]),
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
