"""Dated public-market evidence for the shared US/A-share AI research report."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from html import unescape
import json
import math
import re
from statistics import fmean
from threading import Lock
from time import monotonic
from typing import Any
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree
from zoneinfo import ZoneInfo


CHINA_TZ = ZoneInfo("Asia/Shanghai")
GLOBAL_MARKETS = (
    ("G1", "道琼斯工业指数", "^DJI"), ("G2", "标普500", "^GSPC"),
    ("G3", "纳斯达克综合指数", "^IXIC"), ("G4", "费城半导体指数", "^SOX"),
    ("G5", "VIX", "^VIX"), ("G6", "10年期美债收益率", "^TNX"),
    ("G7", "美元指数", "DX-Y.NYB"),
)
CHINA_INDICES = (
    ("000001", "上证指数", "1.000001", "sh000001"),
    ("399001", "深证成指", "0.399001", "sz399001"),
    ("399006", "创业板指", "0.399006", "sz399006"),
    ("000688", "科创50", "1.000688", "sh000688"),
)
_cache_lock = Lock()
_cached_context: dict[str, Any] | None = None
_cache_until = 0.0
MAX_RESPONSE_BYTES = 2_000_000


def _number(value: Any) -> float | None:
    """Preserve missing vendor fields instead of coercing placeholders to zero."""
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _required_number(value: Any) -> float:
    """Reject an incomplete series when its price or timestamp cannot be verified."""
    result = _number(value)
    if result is None:
        raise ValueError("数据缺少有效数值")
    return result


def _read(url: str) -> bytes:
    """Fetch only configured public feeds with bounded latency and response size."""
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 QuantStudio/0.1", "Referer": "https://quote.eastmoney.com/"})
    with urlopen(request, timeout=5) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("公开数据响应过大")
    return raw


def _json(url: str) -> dict[str, Any]:
    """Require a JSON object before interpreting provider-specific fields."""
    payload = json.loads(_read(url))
    if not isinstance(payload, dict):
        raise ValueError("公开数据格式异常")
    return payload


def _source(source_id: str, label: str, url: str, as_of: str) -> dict[str, str]:
    """Attach a stable citation identifier to the exact evidence request."""
    return {"id": source_id, "label": label, "url": url, "asOf": as_of}


def _percent(latest: float, previous: float | None) -> float | None:
    """Compute a relative move only when a positive comparison price exists."""
    return round((latest / previous - 1) * 100, 3) if previous is not None and previous > 0 else None


def _global_market(source_id: str, name: str, symbol: str) -> dict[str, Any]:
    """Prefer the quote's daily change; never mistake a range-start close for yesterday."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}?range=1mo&interval=1d"
    result = _json(url)["chart"]["result"][0]
    meta = result["meta"]
    timezone = ZoneInfo(meta["exchangeTimezoneName"])
    timestamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]
    rows = sorted({datetime.fromtimestamp(_required_number(stamp), timezone).date().isoformat(): _required_number(close)
                   for stamp, close in zip(timestamps, closes) if _number(close) is not None and _required_number(close) > 0}.items())
    if len(rows) < 2:
        raise ValueError("缺少相邻交易日收盘数据")
    session, latest = rows[-1]
    previous = rows[-2][1]
    stamp = datetime.fromtimestamp(_required_number(meta["regularMarketTime"]), timezone)
    as_of = stamp.isoformat(timespec="seconds") if stamp.date().isoformat() == session else session
    provider_change = _number(meta.get("regularMarketChangePercent"))
    quote_price = _number(meta.get("regularMarketPrice"))
    use_provider_change = (provider_change is not None and provider_change > -100
                           and quote_price is not None and math.isclose(quote_price, latest, rel_tol=1e-5)
                           and stamp.date().isoformat() == session)
    # Continuous dollar quotes may append a partial bar with no preceding daily close.
    # The vendor's quote change is then safer than treating an older valid bar as yesterday.
    reference_close = latest / (1 + provider_change / 100) if use_provider_change else previous
    daily_change = provider_change if use_provider_change else _percent(latest, previous)
    yield_series = symbol == "^TNX"
    return {
        "name": name, "symbol": symbol, "value": round(latest, 4),
        "unit": "%" if yield_series else "点", "sessionDate": session, "asOf": as_of,
        "previousSessionDate": rows[-2][0] if math.isclose(reference_close, previous, rel_tol=1e-4) else None,
        "previousClose": round(reference_close, 6),
        "previousAvailableDailyBar": {"date": rows[-2][0], "close": previous},
        "changePct": None if yield_series else round(daily_change, 3),
        "changeBps": round((latest - reference_close) * 100, 2) if yield_series else None,
        "dailyChangeMethod": "供应商最新报价相对昨收变化" if use_provider_change else "相邻可用日线变化（可能跨缺失交易日）",
        "fiveSessionChangePct": _percent(latest, rows[-6][1]) if len(rows) >= 6 and not yield_series else None,
        "fiveSessionChangeBps": round((latest - rows[-6][1]) * 100, 2) if len(rows) >= 6 and yield_series else None,
        "fiveSessionStartDate": rows[-6][0] if len(rows) >= 6 else None,
        "dailyCloses": [{"date": date, "close": value} for date, value in rows[-6:]],
        "sourceId": source_id, "source": _source(source_id, f"Yahoo Finance · {name}", url, as_of),
        "method": "最新日线值；日变化优先供应商报价涨跌口径，否则为相邻可用日线比较。近5期按5个有效日线间隔、起止日见字段，不保证无缺失交易日。美元指数为跨日连续报价。^TNX数值为收益率百分数，变化用基点。",
    }


def _china_quotes() -> dict[str, Any]:
    """Keep index turnover separate from the non-overlapping Shanghai/Shenzhen total."""
    fields = "f2,f3,f6,f12,f14,f15,f16,f17,f18,f104,f105,f106,f124"
    url = "https://push2.eastmoney.com/api/qt/ulist.np/get?" + urlencode({
        "secids": ",".join(item[2] for item in CHINA_INDICES), "fields": fields, "fltt": 2,
    })
    rows = _json(url)["data"]["diff"]
    if not isinstance(rows, list):
        raise ValueError("指数行情格式异常")
    indices = []
    for code, name, _, _ in CHINA_INDICES:
        row = next((item for item in rows if item.get("f12") == code), None)
        if row is None:
            continue
        latest = _required_number(row.get("f2"))
        if latest <= 0:
            continue
        as_of = datetime.fromtimestamp(_required_number(row.get("f124")), CHINA_TZ).isoformat(timespec="seconds")
        indices.append({
            "code": code, "name": name, "value": latest, "open": _number(row.get("f17")),
            "previousClose": _number(row.get("f18")), "changePct": _number(row.get("f3")),
            "openChangePct": _percent(_number(row.get("f17")), _number(row.get("f18"))) if _number(row.get("f17")) is not None else None,
            "high": _number(row.get("f15")), "low": _number(row.get("f16")),
            "turnoverCny": _number(row.get("f6")), "advancers": _number(row.get("f104")),
            "decliners": _number(row.get("f105")), "unchanged": _number(row.get("f106")),
            "asOf": as_of, "sourceId": "A1",
        })
    if not indices:
        raise ValueError("指数行情为空")
    by_code = {item["code"]: item for item in indices}
    breadth: dict[str, Any] = {"sourceId": "A1", "scope": "上证指数/深证成指接口统计范围（非严格全A，未含北交所）；不叠加创业板、科创50以免重复", "byMarket": []}
    for code in ("000001", "399001"):
        if code in by_code:
            breadth["byMarket"].append({key: by_code[code][key] for key in ("name", "asOf", "advancers", "decliners", "unchanged", "turnoverCny")})
    for field in ("advancers", "decliners", "unchanged", "turnoverCny"):
        values = [by_code.get(code, {}).get(field) for code in ("000001", "399001")]
        same_date = len({by_code.get(code, {}).get("asOf", "")[:10] for code in ("000001", "399001")}) == 1
        breadth[field] = sum(values) if same_date and all(value is not None for value in values) else None
    return {"indices": indices, "breadth": breadth, "source": _source("A1", "东方财富 · A股指数/涨跌统计/成交额", url, min(item["asOf"] for item in indices))}


def reference_levels(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Describe observable price references without declaring forecast support/targets."""
    if not rows:
        return {"availability": "缺少有效历史K线"}
    closes = [row["close"] for row in rows]
    result: dict[str, Any] = {"barCount": len(rows), "firstBar": rows[0]["date"], "lastBar": rows[-1]["date"], "latestClose": closes[-1]}
    for window in (5, 20, 60):
        result[f"return{window}BarsPct"] = _percent(closes[-1], closes[-window - 1]) if len(closes) > window else None
        result[f"sma{window}"] = round(fmean(closes[-window:]), 4) if len(closes) >= window else None
        result[f"prior{window}BarsHigh"] = max(row["high"] for row in rows[-window - 1:-1]) if len(rows) > window else None
        result[f"prior{window}BarsLow"] = min(row["low"] for row in rows[-window - 1:-1]) if len(rows) > window else None
    result["method"] = "前N根高低点排除最新一根；均线含最新一根。仅为历史价格参照，不是预测目标或保证有效的支撑压力。"
    return result


def _china_history(code: str, name: str, secid: str, now: datetime) -> dict[str, Any]:
    """Fetch recent index history and exclude an unclosed current daily candle."""
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get?" + urlencode({
        "secid": secid, "fields1": "f1,f2,f3,f4,f5,f6", "fields2": "f51,f52,f53,f54,f55,f56,f57",
        "klt": 101, "fqt": 0, "beg": (now - timedelta(days=180)).strftime("%Y%m%d"),
        "end": now.strftime("%Y%m%d"), "lmt": 120,
    })
    rows = []
    for line in _json(url)["data"]["klines"]:
        parts = line.split(",")
        day = datetime.strptime(parts[0], "%Y-%m-%d").date()
        if day > now.date() or (day == now.date() and now.hour < 15):
            continue
        open_, close, high, low = [_required_number(value) for value in parts[1:5]]
        if min(open_, close, high, low) > 0 and high >= max(open_, close) and low <= min(open_, close):
            rows.append({"date": day.isoformat(), "open": open_, "close": close, "high": high, "low": low})
    rows = sorted({row["date"]: row for row in rows}.values(), key=lambda row: row["date"])[-90:]
    if not rows:
        raise ValueError("近期已完成日K为空")
    source_id = f"H{code}"
    return {"code": code, "referenceLevels": reference_levels(rows), "recentDailyBars": rows[-10:],
            "sourceId": source_id, "source": _source(source_id, f"东方财富 · {name}已完成日线", url, rows[-1]["date"])}


def _china_intraday(code: str, name: str, market_code: str) -> dict[str, Any]:
    """Retain a bounded actual intraday path, never infer it from daily OHLC alone."""
    url = f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={market_code}"
    data = _json(url)["data"][market_code]["data"]
    day = datetime.strptime(data["date"], "%Y%m%d").date().isoformat()
    points = []
    for line in data["data"]:
        parts = line.split()
        if len(parts) < 2 or not re.fullmatch(r"\d{4}", parts[0]):
            continue
        value = _number(parts[1])
        if value is not None and value > 0:
            points.append({"time": f"{day}T{parts[0][:2]}:{parts[0][2:]}:00+08:00", "price": value})
    points = sorted({point["time"]: point for point in points}.values(), key=lambda point: point["time"])
    if not points:
        raise ValueError("分时路径为空")
    step = max(1, math.ceil(len(points) / 32))
    sampled = points[::step]
    if sampled[-1] != points[-1]:
        sampled.append(points[-1])
    source_id = f"M{code}"
    return {"code": code, "sessionDate": day, "asOf": points[-1]["time"], "sampleCount": len(points),
            "openSample": points[0], "highSample": max(points, key=lambda point: point["price"]),
            "lowSample": min(points, key=lambda point: point["price"]), "path": sampled,
            "method": "腾讯公开逐分钟采样价，抽样展示路径；采样高低点不等于完整逐笔行情高低点。",
            "sourceId": source_id, "source": _source(source_id, f"腾讯 · {name}分时路径", url, points[-1]["time"])}


def _sectors(descending: bool) -> dict[str, Any]:
    """Use industry boards and the provider's main-flow estimate with explicit units."""
    url = "https://push2.eastmoney.com/api/qt/clist/get?" + urlencode({
        "pn": 1, "pz": 5, "po": int(descending), "np": 1, "fltt": 2, "invt": 2,
        "fid": "f3", "fs": "m:90+t:2", "fields": "f12,f14,f3,f62,f124",
    })
    rows = _json(url)["data"]["diff"]
    source_id = "S1" if descending else "S2"
    boards = [{"code": row["f12"], "name": _text(row["f14"], 60), "changePct": _number(row.get("f3")),
               "mainNetInflowCny": _number(row.get("f62")),
               "asOf": datetime.fromtimestamp(_required_number(row.get("f124")), CHINA_TZ).isoformat(timespec="seconds"),
               "sourceId": source_id} for row in rows if _number(row.get("f3")) is not None]
    if not boards:
        raise ValueError("行业排行为空")
    return {"boards": boards, "scope": "东方财富行业板块；按涨跌幅排序，金额为供应商按成交单规模估算的主力净流入，非机构账户实测，板块金额不可相加为全市场净流入。",
            "source": _source(source_id, f"东方财富 · 行业{'领涨' if descending else '领跌'}排行", url, min(row["asOf"] for row in boards))}


def _main_flows() -> dict[str, Any]:
    """Keep the vendor's Shanghai/Shenzhen large-order net-flow convention explicit."""
    url = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get?" + urlencode({
        "lmt": 5, "klt": 101, "secid": "1.000001", "secid2": "0.399001",
        "fields1": "f1,f2,f3,f7", "fields2": "f51,f52,f53,f54,f55,f56",
    })
    rows = []
    for line in _json(url)["data"]["klines"]:
        parts = line.split(",")
        if len(parts) >= 6:
            day = datetime.strptime(parts[0], "%Y-%m-%d").date().isoformat()
            rows.append({"date": day, "mainNetInflowCny": _required_number(parts[1]),
                         "superLargeNetInflowCny": _number(parts[4]), "largeNetInflowCny": _number(parts[5])})
    rows.sort(key=lambda row: row["date"])
    if not rows:
        raise ValueError("主力资金数据为空")
    return {"observations": rows[-5:], "sourceId": "F1",
            "scope": "东方财富沪深合并大单/超大单资金估算；单位人民币元，正为净流入，负为净流出。不是机构账户资金或北向净买入。交易日内为当日累计，接口只提供日期，精确更新时间未知。",
            "source": _source("F1", "东方财富 · 沪深主力资金估算", url, rows[-1]["date"])}


def _text(value: Any, limit: int = 500) -> str:
    """Bound third-party excerpts and remove markup before sending them as evidence."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(str(value or "")))).strip()[:limit]


def _news_url(value: Any) -> str | None:
    """Expose only normal HTTP(S) citation links; article URLs are never fetched."""
    url = str(value or "").strip()
    parts = urlsplit(url)
    return url if parts.scheme in {"http", "https"} and parts.hostname and not parts.username and not parts.password else None


def _sina_news(now: datetime) -> dict[str, Any]:
    """Read dated domestic finance headlines without claiming a complete news archive."""
    url = "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2516&num=100&page=1"
    events = []
    for row in _json(url)["result"]["data"]:
        published = datetime.fromtimestamp(_required_number(row["ctime"]), CHINA_TZ)
        link = _news_url(row.get("url"))
        title = _text(row.get("title"), 180)
        if now - timedelta(days=7) <= published <= now and link and title:
            events.append({"title": title, "summary": _text(row.get("intro"), 360), "url": link,
                           "publishedAt": published.isoformat(timespec="seconds"), "publisher": _text(row.get("media_name"), 80) or "新浪财经", "region": "CN"})
    if not events:
        raise ValueError("近7天新闻为空")
    return {"events": events, "source": _source("NCN", "新浪财经 · 最近财经资讯", url, max(event["publishedAt"] for event in events))}


def _rss_news(url: str, name: str, source_id: str, region: str, now: datetime) -> dict[str, Any]:
    """Require dated RSS items and keep headlines distinct from verified event causality."""
    root = ElementTree.fromstring(_read(url))
    events = []
    for item in root.findall("./channel/item")[:100]:
        raw_date = item.findtext("pubDate")
        if not raw_date:
            continue
        try:
            published = parsedate_to_datetime(raw_date)
            if published.tzinfo is None:
                continue
            published = published.astimezone(CHINA_TZ)
            link = _news_url(item.findtext("link"))
            title = _text(item.findtext("title"), 180)
            if now - timedelta(days=7) <= published <= now and link and title:
                events.append({"title": title, "summary": _text(item.findtext("description"), 360), "url": link,
                               "publishedAt": published.isoformat(timespec="seconds"), "publisher": _text(item.findtext("source"), 80) or name, "region": region})
        except (ValueError, TypeError, OverflowError):
            continue
    if not events:
        raise ValueError("近7天可核验日期的新闻为空")
    return {"events": events, "source": _source(source_id, name, url, max(event["publishedAt"] for event in events))}


def _select_events(feeds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prefer macro/market drivers while keeping room for both domestic and US evidence."""
    keywords = re.compile(r"央行|降息|加息|财政|政策|经济|通胀|关税|美联储|半导体|芯片|科技|A股|股市|成交|资金|证券|证监|消费|地产|利率|国债|美元|就业|英伟达|fed\b|rates?\b|inflation|tariff|treasur|dollar|jobs|stocks?|market|chip|semiconductor|nvidia|earnings|economy", re.I)
    unique = {event["url"]: event for feed in feeds for event in feed.get("events", [])}
    selected = []
    for region in ("CN", "US"):
        events = [event for event in unique.values() if event["region"] == region]
        events.sort(key=lambda event: (len(keywords.findall(event["title"])), event["publishedAt"]), reverse=True)
        selected.extend(events[:10])
    return [{**event, "sourceId": f"N{index}"} for index, event in enumerate(selected, 1)]


def fetch_market_context() -> dict[str, Any]:
    """Collect independent evidence concurrently and cache complete/partial snapshots briefly."""
    global _cached_context, _cache_until
    with _cache_lock:
        if _cached_context is not None and monotonic() < _cache_until:
            return deepcopy(_cached_context)
    now = datetime.now(CHINA_TZ)
    tasks: dict[str, tuple[Any, tuple[Any, ...], str]] = {}
    for source_id, name, symbol in GLOBAL_MARKETS:
        tasks[source_id] = (_global_market, (source_id, name, symbol), name)
    tasks["china"] = (_china_quotes, (), "A股指数与市场广度")
    for code, name, secid, market_code in CHINA_INDICES:
        tasks[f"H{code}"] = (_china_history, (code, name, secid, now), f"{name}历史日线")
        tasks[f"M{code}"] = (_china_intraday, (code, name, market_code), f"{name}分时路径")
    tasks["leaders"] = (_sectors, (True,), "领涨行业")
    tasks["laggards"] = (_sectors, (False,), "领跌行业")
    tasks["flows"] = (_main_flows, (), "主力资金")
    tasks["cnnews"] = (_sina_news, (now,), "国内财经新闻")
    tasks["usnews"] = (_rss_news, ("https://www.cnbc.com/id/100003114/device/rss/rss.html", "CNBC · US Top News", "NUS", "US", now), "美国财经新闻")
    google_url = "https://news.google.com/rss/search?" + urlencode({"q": "A股 央行 政策 when:7d", "hl": "zh-CN", "gl": "CN", "ceid": "CN:zh-Hans"})
    tasks["cnrecent"] = (_rss_news, (google_url, "Google News · 国内政策与市场", "NRECENT", "CN", now), "近7天国内事件检索")
    results: dict[str, dict[str, Any]] = {}
    warnings = ["北向净买入未接入可核验披露；公开额度/零值字段不等于净流入，不能据此判断北向资金方向。",
                "新闻仅覆盖公开资讯源返回且发布时间在近7天内的条目，并非完整事件库；摘要与标题不能单独证实涨跌原因。"]
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(function, *args): (key, label) for key, (function, args, label) in tasks.items()}
        for future in as_completed(futures):
            key, label = futures[future]
            try:
                results[key] = future.result()
            except Exception:
                # One unavailable feed must not erase independently verified market evidence.
                warnings.append(f"{label}暂不可用；本次不据此下结论。")
    sources = [result["source"] for result in results.values()]
    for code, name, _, _ in CHINA_INDICES:
        if not any(item["code"] == code for item in results.get("china", {}).get("indices", [])):
            warnings.append(f"{name}最新指数行情缺失。")
    events = _select_events([results.get(key, {}) for key in ("cnnews", "cnrecent", "usnews")])
    sources.extend(_source(event["sourceId"], event["title"], event["url"], event["publishedAt"]) for event in events)
    # Expose older sessions explicitly rather than calling the retrieval time the quote time.
    for source in sources:
        try:
            day = datetime.fromisoformat(source["asOf"]).date()
            if (now.date() - day).days > (8 if source["id"].startswith("N") else 4):
                warnings.append(f"{source['label']}数据截至{source['asOf']}，时效不足，不代表当前盘面。")
        except ValueError:
            warnings.append(f"{source['label']}时间无法核验。")
    china = results.get("china", {})
    context = {
        "retrievedAt": now.isoformat(timespec="seconds"),
        "newsWindow": {"from": (now - timedelta(days=7)).isoformat(timespec="seconds"), "to": now.isoformat(timespec="seconds")},
        "globalMarkets": [{key: value for key, value in results[source_id].items() if key != "source"} for source_id, _, _ in GLOBAL_MARKETS if source_id in results],
        "chinaMarket": {"indices": china.get("indices", []), "breadth": china.get("breadth"),
                        "dailyStructure": [{key: value for key, value in results[f'H{code}'].items() if key != "source"} for code, _, _, _ in CHINA_INDICES if f"H{code}" in results],
                        "intraday": [{key: value for key, value in results[f'M{code}'].items() if key != "source"} for code, _, _, _ in CHINA_INDICES if f"M{code}" in results]},
        "fundFlows": {"main": {key: value for key, value in results.get("flows", {}).items() if key != "source"} or None,
                      "northbound": {"available": False, "netInflowCny": None, "reason": warnings[0]}},
        "sectors": {key: {field: value for field, value in results.get(key, {}).items() if field != "source"} or None for key in ("leaders", "laggards")},
        "events": events, "sources": sorted(sources, key=lambda source: source["id"]),
        "warnings": sorted(set(warnings)),
    }
    with _cache_lock:
        _cached_context = deepcopy(context)
        _cache_until = monotonic() + (120 if len(results) == len(tasks) else 30)
    return context
