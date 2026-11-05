"""Public market data adapter for the first A-share and ETF workflow."""

from __future__ import annotations

import json
from datetime import datetime, time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd
import talib


DEFAULT_SYMBOL = "159977"


def fetch_bars(symbol: str, timeframe: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Fetch normalized intraday or adjusted K-line bars from Tencent's public feed."""
    code = normalize_symbol(symbol)
    market_code = f"{_exchange_prefix(symbol)}{code}"
    received_at = datetime.now()
    market_open = is_market_open(received_at)
    period_types = {"daily": "day", "weekly": "week", "monthly": "month"}
    if timeframe in {"intraday", "1m"}:
        url = f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={market_code}"
        payload = _get_json(url)
        instrument = payload.get("data", {}).get(market_code, {})
        minute_rows = instrument.get("data", {}).get("data", [])
        rows = _minute_rows(minute_rows, instrument.get("data", {}).get("date"))
        columns = ("datetime", "open", "high", "low", "close", "volume")
        sampled = True
    elif timeframe == "five_day":
        url = f"https://web.ifzq.gtimg.cn/appstock/app/day/query?code={market_code}"
        payload = _get_json(url)
        instrument = payload.get("data", {}).get(market_code, {})
        rows = _five_day_rows(instrument.get("data", []))
        columns = ("datetime", "open", "high", "low", "close", "volume")
        sampled = True
    elif timeframe in period_types:
        period = period_types[timeframe]
        url = (
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
            f"?param={market_code},{period},,,260,qfq"
        )
        payload = _get_json(url)
        instrument = payload.get("data", {}).get(market_code, {})
        rows = _kline_ohlcv_rows(instrument.get(f"qfq{period}") or instrument.get(period, []))
        columns = ("datetime", "open", "close", "high", "low", "volume")
        sampled = False
    else:
        raise ValueError("支持分时、五日、日K、周K和月K")

    if not rows:
        raise RuntimeError("腾讯公开行情接口没有返回K线")
    bars = pd.DataFrame(rows, columns=columns)
    bars["datetime"] = pd.to_datetime(bars["datetime"], format="mixed", errors="coerce")
    for name in ("open", "high", "low", "close", "volume"):
        bars[name] = pd.to_numeric(bars[name], errors="coerce")
    bars = (
        bars.dropna(subset=["datetime", "open", "high", "low", "close", "volume"])
        .sort_values("datetime")
        .drop_duplicates("datetime", keep="last")
        .reset_index(drop=True)
    )

    quote_fields = instrument.get("qt", {}).get(market_code, [])
    quote = _quote(quote_fields)
    if quote and market_open:
        quote_time = pd.Timestamp(quote["timestamp"])
        if timeframe in {"daily", "weekly", "monthly"}:
            row_index = _current_period_index(bars, quote_time, timeframe)
            if row_index is None and timeframe == "daily":
                row_index = len(bars)
                bars.loc[row_index] = {
                    "datetime": quote_time, "open": quote["open"],
                    "high": quote["high"], "low": quote["low"],
                    "close": quote["price"], "volume": quote["volume"],
                }
            elif row_index is not None:
                bars.loc[row_index, "close"] = quote["price"]
                bars.loc[row_index, "high"] = max(bars.loc[row_index, "high"], quote["high"])
                bars.loc[row_index, "low"] = min(bars.loc[row_index, "low"], quote["low"])
                if timeframe == "daily":
                    bars.loc[row_index, "open"] = quote["open"]
                    bars.loc[row_index, "volume"] = quote["volume"]
                else:
                    bars.loc[row_index, "datetime"] = quote_time.normalize()
        elif not bars.empty:
            row_index = bars.index[-1]
            bars.loc[row_index, "datetime"] = quote_time.floor("min")
            bars.loc[row_index, ["open", "high", "low", "close"]] = quote["price"]

    bars = (
        bars.dropna(subset=["datetime", "open", "high", "low", "close", "volume"])
        .sort_values("datetime")
        .drop_duplicates("datetime", keep="last")
        .reset_index(drop=True)
    )
    if timeframe not in {"intraday", "1m"} and len(bars) < 60:
        raise RuntimeError(f"有效K线不足，当前仅有 {len(bars)} 根，至少需要 60 根才能计算 SMA60")
    last_bar_time = bars.iloc[-1]["datetime"]
    if (
        timeframe in {"daily", "weekly", "monthly"}
        and not market_open
        and last_bar_time.date() == received_at.date()
        and last_bar_time.time() == time(0, 0)
    ):
        last_bar_time = last_bar_time.replace(hour=15)
        bars.loc[bars.index[-1], "datetime"] = last_bar_time
    source_time = quote["timestamp"] if quote and market_open else last_bar_time.isoformat(sep=" ")
    metadata = {
        "source": "Tencent public quote",
        "sourceTime": str(source_time),
        "dayChangePct": (
            (quote["price"] / quote["previousClose"] - 1) * 100
            if quote and quote["previousClose"] else None
        ),
        "exchange": market_code[:2].upper(),
        "sampled": sampled,
        "marketOpen": market_open,
        "sessionComplete": (
            timeframe in {"intraday", "1m"}
            and last_bar_time.time() >= time(15, 0)
            and (last_bar_time.date() < received_at.date() or received_at.time() >= time(15, 0))
        ),
        "sessionStatus": "交易时段" if market_open else "非交易时段 · 显示最近有效行情",
        "delayNotice": "公开行情可能延迟，不保证交易级实时性",
    }
    return bars, metadata


def fetch_daily_history(symbol: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Fetch adjusted daily bars, falling back to Yahoo when Eastmoney is unavailable."""
    code = normalize_symbol(symbol)
    market = _exchange_prefix(symbol)
    secid = f"{'1' if market == 'sh' else '0'}.{code}"
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={secid}&fields1=f1%2Cf2%2Cf3%2Cf4%2Cf5%2Cf6"
        "&fields2=f51%2Cf52%2Cf53%2Cf54%2Cf55%2Cf56%2Cf57"
        "&klt=101&fqt=1&beg=19900101&end=20500101&lmt=10000"
    )
    request = Request(url, headers={
        "Referer": "https://quote.eastmoney.com/",
        "User-Agent": "Mozilla/5.0",
    })
    try:
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
        data = payload.get("data") or {}
        rows = data.get("klines") or []
        if not rows:
            raise RuntimeError(f"{code} 的东方财富公开历史日线为空")
        parsed_rows = [row.split(",")[:6] for row in rows if isinstance(row, str) and len(row.split(",")) >= 6]
        bars = pd.DataFrame(parsed_rows, columns=("datetime", "open", "close", "high", "low", "volume"))
        bars["datetime"] = pd.to_datetime(bars["datetime"], errors="coerce")
        for name in ("open", "high", "low", "close", "volume"):
            bars[name] = pd.to_numeric(bars[name], errors="coerce")
        bars = (
            bars.dropna(subset=["datetime", "open", "high", "low", "close", "volume"])
            .loc[lambda frame: (frame[["open", "high", "low", "close"]] > 0).all(axis=1) & (frame["volume"] >= 0)]
            .sort_values("datetime")
            .drop_duplicates("datetime", keep="last")
            .reset_index(drop=True)
        )
        if bars.empty:
            raise RuntimeError(f"{code} 的东方财富有效历史日线为空")
        return bars, _history_metadata(bars, market, "Eastmoney public adjusted daily history", "前复权")
    except (HTTPError, URLError, TimeoutError, ConnectionError, json.JSONDecodeError, RuntimeError) as eastmoney_error:
        try:
            return _fetch_yahoo_daily_history(code, market)
        except (HTTPError, URLError, TimeoutError, ConnectionError, json.JSONDecodeError, KeyError, TypeError, ValueError, RuntimeError) as yahoo_error:
            raise RuntimeError(
                f"历史行情获取失败：东方财富（{eastmoney_error}）；Yahoo Finance（{yahoo_error}）"
            ) from yahoo_error


def _fetch_yahoo_daily_history(code: str, market: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Fetch Yahoo chart data and apply its adjusted-close factor to OHLC prices."""
    symbol = f"{code}.{market.upper()}"
    period_end = int(datetime.now().timestamp()) + 86_400
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?period1=0&period2={period_end}&interval=1d&events=div%2Csplits"
    )
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    result = (payload.get("chart", {}).get("result") or [None])[0]
    if not result:
        raise RuntimeError(f"{symbol} 的 Yahoo 历史日线为空")
    quote = result["indicators"]["quote"][0]
    adjusted_close = result["indicators"].get("adjclose", [{}])[0].get("adjclose")
    if not adjusted_close:
        raise RuntimeError(f"{symbol} 的 Yahoo 响应缺少复权收盘价")
    bars = pd.DataFrame({
        "datetime": pd.to_datetime(result["timestamp"], unit="s", utc=True)
        .tz_convert("Asia/Shanghai").tz_localize(None),
        "open": quote["open"],
        "high": quote["high"],
        "low": quote["low"],
        "close": quote["close"],
        "adjustedClose": adjusted_close,
        "volume": quote["volume"],
    })
    bars = bars.dropna(subset=["datetime", "open", "high", "low", "close", "adjustedClose", "volume"])
    factor = bars["adjustedClose"] / bars["close"]
    for name in ("open", "high", "low", "close"):
        bars[name] = pd.to_numeric(bars[name], errors="coerce") * factor
    bars["volume"] = pd.to_numeric(bars["volume"], errors="coerce")
    bars = (
        bars.drop(columns="adjustedClose")
        .dropna(subset=["datetime", "open", "high", "low", "close", "volume"])
        .loc[lambda frame: (frame[["open", "high", "low", "close"]] > 0).all(axis=1) & (frame["volume"] >= 0)]
        .sort_values("datetime")
        .drop_duplicates("datetime", keep="last")
        .reset_index(drop=True)
    )
    if bars.empty:
        raise RuntimeError(f"{symbol} 的 Yahoo 有效历史日线为空")
    return bars, _history_metadata(bars, market, "Yahoo Finance public adjusted daily history", "adjclose OHLC factor")


def _history_metadata(bars: pd.DataFrame, market: str, source: str, adjustment: str) -> dict[str, Any]:
    """Build consistent source metadata for adjusted historical daily feeds."""
    return {
        "source": source,
        "sourceTime": bars.iloc[-1]["datetime"].isoformat(sep=" "),
        "exchange": market.upper(),
        "adjustment": adjustment,
        "barCount": len(bars),
        "startTime": bars.iloc[0]["datetime"].isoformat(sep=" "),
    }


def normalize_symbol(symbol: str) -> str:
    """Normalize common A-share symbol suffixes to a six-digit code."""
    code = symbol.upper()
    for suffix in (".SZSE", ".SSE", ".SZ", ".SH"):
        code = code.removesuffix(suffix)
    if len(code) != 6 or not code.isdigit():
        raise ValueError("首期仅支持六位 A 股和 ETF 代码，例如 159977")
    return code


def _exchange_prefix(symbol: str) -> str:
    """Select the public endpoint market from a suffix or common A-share prefix."""
    normalized = symbol.upper()
    if normalized.endswith((".SH", ".SSE")):
        return "sh"
    if normalized.endswith((".SZ", ".SZSE")):
        return "sz"
    code = normalize_symbol(symbol)
    return "sh" if code.startswith(("5", "6", "9")) else "sz"


def _current_period_index(bars: pd.DataFrame, quote_time: pd.Timestamp, timeframe: str) -> int | None:
    """Find the daily, weekly or monthly bar containing a live quote timestamp."""
    for index, bar_time in bars["datetime"].items():
        if timeframe == "daily" and bar_time.date() == quote_time.date():
            return int(index)
        if timeframe == "weekly" and bar_time.isocalendar()[:2] == quote_time.isocalendar()[:2]:
            return int(index)
        if timeframe == "monthly" and (bar_time.year, bar_time.month) == (quote_time.year, quote_time.month):
            return int(index)
    return None


def _kline_ohlcv_rows(rows: list[list[Any]]) -> list[list[Any]]:
    """Keep OHLCV fields and ignore optional corporate-action metadata columns."""
    return [row[:6] for row in rows if isinstance(row, (list, tuple)) and len(row) >= 6]


def is_market_open(now: datetime | None = None) -> bool:
    """Approximate the standard A-share session using the computer's local clock."""
    current = now or datetime.now()
    if current.weekday() >= 5:
        return False
    current_time = current.time()
    return time(9, 30) <= current_time <= time(11, 30) or time(13, 0) <= current_time <= time(15, 0)


def analyze(bars: pd.DataFrame) -> dict[str, Any]:
    """Calculate trend, momentum, volatility and explanatory signal fields."""
    close = bars["close"].to_numpy(dtype=np.float64)
    high = bars["high"].to_numpy(dtype=np.float64)
    low = bars["low"].to_numpy(dtype=np.float64)
    volume = bars["volume"].to_numpy(dtype=np.float64)
    sma20 = talib.SMA(close, timeperiod=20)
    sma60 = talib.SMA(close, timeperiod=60)
    _, _, macd = talib.MACD(close, fastperiod=12, slowperiod=26, signalperiod=9)
    rsi = talib.RSI(close, timeperiod=14)
    atr = talib.ATR(high, low, close, timeperiod=20)
    last = len(bars) - 1
    values = [sma20[last], sma60[last], macd[last], rsi[last], atr[last]]
    if not np.isfinite(values).all():
        raise RuntimeError("指标预热数据不足，无法生成分析")
    trend = close[last] > sma60[last]
    momentum = macd[last] > 0
    volume_expansion = (
        len(bars) >= 30
        and np.mean(volume[last - 4:last + 1]) > np.mean(volume[last - 29:last + 1]) * 1.25
    )
    breakout = len(bars) >= 71 and close[last] > np.max(high[last - 70:last])
    conditions = [trend, momentum, volume_expansion or breakout]
    reasons = []
    reasons.append("价格站在 SMA60 上方" if trend else "价格低于 SMA60，中期趋势过滤未通过")
    reasons.append("MACD 动能为正" if momentum else "MACD 动能为负")
    if volume_expansion:
        reasons.append("成交量较近期均值放大")
    elif breakout:
        reasons.append("价格突破近 70 根K线高点")
    else:
        reasons.append("尚未出现量能放大或区间突破")
    previous = close[last - 1]
    change = (close[last] / previous - 1) * 100 if previous else 0
    return {
        "price": float(close[last]),
        "changePct": float(change),
        "sma20": float(sma20[last]),
        "sma60": float(sma60[last]),
        "macd": float(macd[last]),
        "rsi14": float(rsi[last]),
        "atr20": float(atr[last]),
        "trend": bool(trend),
        "momentum": bool(momentum),
        "volumeExpansion": bool(volume_expansion),
        "breakout": bool(breakout),
        "signal": "满足入场条件" if all(conditions) else "等待条件确认",
        "assessment": "；".join(reasons),
    }


def _minute_rows(rows: list[str], trading_date: str | None = None) -> list[dict[str, Any]]:
    """Convert cumulative minute feed rows into sampled-price bars."""
    result = []
    cumulative_before = 0.0
    day = trading_date or datetime.now().strftime("%Y-%m-%d")
    if len(day) == 8 and day.isdigit():
        day = f"{day[:4]}-{day[4:6]}-{day[6:]}"
    for raw in rows:
        fields = raw.split()
        if len(fields) < 3:
            continue
        minute_of_day = int(fields[0])
        if not (930 <= minute_of_day <= 1130 or 1300 <= minute_of_day <= 1500):
            continue
        cumulative = float(fields[2])
        price = float(fields[1])
        result.append({
            "datetime": f"{day} {fields[0][:2]}:{fields[0][2:]}:00",
            "open": price, "high": price, "low": price, "close": price,
            "volume": max(cumulative - cumulative_before, 0.0),
        })
        cumulative_before = cumulative
    return result


def _five_day_rows(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Parse up to five dated intraday sessions, resetting volume per session."""
    bars = []
    for session in sessions[-5:]:
        day = session.get("date", "")
        bars.extend(_minute_rows(session.get("data", []), day))
    return bars


def _quote(fields: list[str]) -> dict[str, Any] | None:
    """Read the live quote fields when the source supplies a valid timestamp."""
    if len(fields) <= 34 or len(fields[30]) != 14:
        return None
    try:
        timestamp = datetime.strptime(fields[30], "%Y%m%d%H%M%S").isoformat(sep=" ")
        return {
            "timestamp": timestamp,
            "price": float(fields[3]), "previousClose": float(fields[4]), "open": float(fields[5]),
            "volume": float(fields[6]), "high": float(fields[33]),
            "low": float(fields[34]),
        }
    except (ValueError, IndexError):
        return None


def _get_json(url: str) -> dict[str, Any]:
    """Request public quote data with a bounded timeout and browser headers."""
    request = Request(url, headers={"Referer": "https://gu.qq.com/", "User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))
