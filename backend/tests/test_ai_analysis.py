"""Verify market evidence semantics and the AI report's real local HTTP contract."""

from datetime import datetime
from io import BytesIO
import json
import socket
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import uvicorn

from app import ai_context
from app.ai_analysis import build_research_messages
from app.ai_context import CHINA_TZ, _china_history, _china_intraday, _china_quotes, _global_market, _main_flows, _rss_news, fetch_market_context, reference_levels
from app.main import AISettings, app


def market_fixture() -> dict:
    """Keep integration tests independent of real markets, credentials and user files."""
    return {
        "retrievedAt": "2026-11-19T10:00:00+08:00", "newsWindow": {},
        "globalMarkets": [{"name": "VIX", "value": 20, "sourceId": "G5"}],
        "chinaMarket": {"indices": []}, "fundFlows": {"northbound": {"available": False}},
        "sectors": {}, "events": [], "warnings": ["北向净买入无法核验"],
        "sources": [{"id": "G5", "label": "VIX", "url": "https://example.org/vix", "asOf": "2026-11-18"}],
    }


class EvidenceTest(unittest.TestCase):
    """Protect trading dates, comparison windows, units and missing-data behavior."""

    def test_model_id_is_trimmed_without_changing_provider_identifier(self) -> None:
        """Keep provider-specific names intact while removing accidental outer spaces."""
        settings = AISettings(baseUrl="https://model.example/v1", model="  provider/model-v2  ")
        self.assertEqual(settings.model, "provider/model-v2")

    def test_global_changes_use_adjacent_sessions_and_basis_points(self) -> None:
        """A range-start reference must not replace the actual previous daily close."""
        stamps = [int(datetime(2026, 11, day, 15, tzinfo=CHINA_TZ).timestamp()) for day in (10, 11, 12, 13, 16, 17)]
        payload = {"chart": {"result": [{
            "meta": {"exchangeTimezoneName": "America/New_York", "regularMarketTime": stamps[-1], "chartPreviousClose": 999},
            "timestamp": stamps, "indicators": {"quote": [{"close": [4.0, 4.1, 4.2, 4.3, 4.4, 4.5]}]},
        }]}}
        with patch("app.ai_context._json", return_value=payload):
            yield_data = _global_market("G6", "10年美债", "^TNX")
            index_data = _global_market("G2", "标普500", "^GSPC")
        self.assertEqual(yield_data["changeBps"], 10)
        self.assertEqual(yield_data["fiveSessionChangeBps"], 50)
        self.assertIsNone(yield_data["changePct"])
        self.assertEqual(index_data["changePct"], 2.273)
        self.assertEqual(index_data["fiveSessionChangePct"], 12.5)
        self.assertEqual(index_data["previousSessionDate"], "2026-11-16")

    def test_breadth_and_turnover_do_not_double_count_subindices(self) -> None:
        """Only the Shanghai/Shenzhen rows contribute to the explicitly scoped total."""
        stamp = int(datetime(2026, 11, 19, 10, tzinfo=CHINA_TZ).timestamp())
        rows = [{"f12": code, "f2": 100, "f17": 101, "f18": 100, "f6": amount,
                 "f104": 10, "f105": 20, "f106": "-", "f124": stamp}
                for code, amount in (("000001", 1e8), ("399001", 2e8), ("399006", 9e8), ("000688", 9e8))]
        with patch("app.ai_context._json", return_value={"data": {"diff": rows}}):
            result = _china_quotes()
        self.assertEqual(result["breadth"]["turnoverCny"], 3e8)
        self.assertEqual(result["breadth"]["advancers"], 20)
        self.assertIsNone(result["breadth"]["unchanged"])
        self.assertEqual(len(result["indices"]), 4)

    def test_continuous_dollar_quote_prefers_verified_provider_day_change(self) -> None:
        """Missing prior daily data must not turn an older close into today's baseline."""
        stamps = [int(datetime(2026, 11, day, 20, tzinfo=CHINA_TZ).timestamp()) for day in (17, 18, 19)]
        payload = {"chart": {"result": [{
            "meta": {"exchangeTimezoneName": "America/New_York", "regularMarketTime": stamps[-1],
                     "regularMarketPrice": 103.8, "regularMarketChangePercent": -0.05},
            "timestamp": stamps, "indicators": {"quote": [{"close": [103.4, None, 103.8]}]},
        }]}}
        with patch("app.ai_context._json", return_value=payload):
            result = _global_market("G7", "美元", "DX-Y.NYB")
        self.assertEqual(result["changePct"], -0.05)
        self.assertIsNone(result["previousSessionDate"])
        self.assertGreater(result["previousClose"], result["value"])

    def test_mismatched_trading_dates_do_not_produce_an_aggregate(self) -> None:
        """A stale market must not be added to another market's current session."""
        rows = [{"f12": code, "f2": 100, "f6": 1000, "f124": int(datetime(2026, 11, day, 10, tzinfo=CHINA_TZ).timestamp())}
                for code, day in (("000001", 18), ("399001", 19))]
        with patch("app.ai_context._json", return_value={"data": {"diff": rows}}):
            result = _china_quotes()
        self.assertIsNone(result["breadth"]["turnoverCny"])

    def test_reference_ranges_exclude_latest_and_require_full_window(self) -> None:
        """A new high cannot become its own prior resistance reference."""
        rows = [{"date": str(index), "close": 10, "high": 11, "low": 9} for index in range(6)]
        rows[-1].update(close=20, high=21)
        levels = reference_levels(rows)
        self.assertEqual(levels["prior5BarsHigh"], 11)
        self.assertEqual(levels["sma5"], 12)
        self.assertIsNone(levels["prior20BarsHigh"])

    def test_index_daily_history_excludes_open_session(self) -> None:
        """Current intraday index extremes cannot masquerade as a confirmed daily bar."""
        payload = {"data": {"klines": ["2026-11-18,10,11,12,9,100,1000", "2026-11-19,11,12,13,10,100,1000"]}}
        with patch("app.ai_context._json", return_value=payload):
            result = _china_history("000001", "上证", "1.000001", datetime(2026, 11, 19, 10, tzinfo=CHINA_TZ))
        self.assertEqual(result["referenceLevels"]["lastBar"], "2026-11-18")
        self.assertEqual(result["referenceLevels"]["barCount"], 1)

    def test_intraday_preserves_observed_extremes_and_last_sample(self) -> None:
        """Downsampling the path must retain true sampled extrema separately."""
        points = [f"{9 + (30 + index) // 60:02d}{(30 + index) % 60:02d} {100 + index} 100" for index in range(100)]
        payload = {"data": {"sh000001": {"data": {"date": "20261119", "data": points}}}}
        with patch("app.ai_context._json", return_value=payload):
            result = _china_intraday("000001", "上证", "sh000001")
        self.assertLessEqual(len(result["path"]), 33)
        self.assertEqual(result["sampleCount"], 100)
        self.assertEqual(result["highSample"]["price"], 199)
        self.assertEqual(result["path"][-1]["price"], 199)

    def test_flow_amounts_preserve_currency_units_and_sign(self) -> None:
        """Net outflow stays negative CNY and is never relabeled as northbound buying."""
        with patch("app.ai_context._json", return_value={"data": {"klines": ["2026-11-19,-300000000,1,2,-100000000,-200000000"]}}):
            result = _main_flows()
        self.assertEqual(result["observations"][0]["mainNetInflowCny"], -3e8)
        self.assertIn("不是", result["scope"])

    def test_news_excludes_old_future_undated_and_unsafe_items(self) -> None:
        """Only timestamped recent evidence and ordinary web citations reach the model."""
        xml = b'''<rss><channel>
          <item><title>Fed &amp; rates</title><link>https://example.org/recent</link><pubDate>Wed, 18 Nov 2026 12:00:00 GMT</pubDate><description>&lt;b&gt;Evidence&lt;/b&gt;</description></item>
          <item><title>Old</title><link>https://example.org/old</link><pubDate>Sun, 01 Nov 2026 12:00:00 GMT</pubDate></item>
          <item><title>Future</title><link>https://example.org/future</link><pubDate>Fri, 20 Nov 2026 12:00:00 GMT</pubDate></item>
          <item><title>Undated</title><link>https://example.org/none</link></item>
          <item><title>Unsafe</title><link>javascript:alert(1)</link><pubDate>Wed, 18 Nov 2026 12:00:00 GMT</pubDate></item>
        </channel></rss>'''
        with patch("app.ai_context._read", return_value=xml):
            result = _rss_news("https://example.org/rss", "News", "NUS", "US", datetime(2026, 11, 19, 10, tzinfo=CHINA_TZ))
        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(result["events"][0]["summary"], "Evidence")

    def test_all_unavailable_feeds_are_explicit_and_short_cached(self) -> None:
        """Source outages cannot invent zeros or prevent a bounded partial report."""
        with patch.object(ai_context, "_cached_context", None), patch.object(ai_context, "_cache_until", 0), patch("app.ai_context._read", side_effect=TimeoutError):
            first = fetch_market_context()
            first["warnings"].clear()
            second = fetch_market_context()
        self.assertEqual(second["globalMarkets"], [])
        self.assertIsNone(second["fundFlows"]["main"])
        self.assertFalse(second["fundFlows"]["northbound"]["available"])
        self.assertIsNone(second["fundFlows"]["northbound"]["netInflowCny"])
        self.assertGreater(len(second["warnings"]), 20)

    def test_prompt_covers_requested_report_and_untrusted_evidence(self) -> None:
        """Both UI entry points receive the same six-section evidence-first contract."""
        messages = build_research_messages({"selectedStrategy": {"supported": False}}, market_fixture())
        prompt = json.loads(messages[1]["content"])
        self.assertEqual(len(prompt["reportSections"]), 6)
        self.assertIn("不是指令", messages[0]["content"])
        self.assertIn("基点", prompt["definitions"]["usChanges"])
        self.assertIn("仓位", prompt["reportSections"][4]["requirements"])


class AIReportHTTPTest(unittest.TestCase):
    """Exercise real FastAPI HTTP responses without paid models or user credentials."""

    @classmethod
    def setUpClass(cls) -> None:
        """Start an isolated loopback server with a ten-second readiness deadline."""
        cls.sock = socket.socket()
        cls.sock.bind(("127.0.0.1", 0))
        cls.url = f"http://127.0.0.1:{cls.sock.getsockname()[1]}"
        cls.server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
        cls.thread = threading.Thread(target=cls.server.run, kwargs={"sockets": [cls.sock]}, daemon=True)
        cls.thread.start()
        deadline = time.monotonic() + 10
        while not cls.server.started and cls.thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.02)
        if not cls.server.started:
            cls.server.should_exit = True
            cls.thread.join(timeout=5)
            cls.sock.close()
            raise RuntimeError("AI test server failed to start")

    @classmethod
    def tearDownClass(cls) -> None:
        """Close the test server and its listening socket after verification."""
        cls.server.should_exit = True
        cls.thread.join(timeout=5)
        cls.sock.close()

    def setUp(self) -> None:
        """Replace credential reads and external market collection for each request."""
        self.payload = {"symbol": "159977", "timeframe": "daily", "sourceTime": "2026-11-19 10:00:00", "analysis": {"price": 1.5},
                        "strategy": {"supported": True, "signal": "等待", "selectedConfiguration": {"strategyType": "trend_filter"}}, "bars": []}
        patches = [patch("app.main.get_ai_settings", return_value={"baseUrl": "https://model.example/v1", "model": "fixture"}),
                   patch("app.main.keyring.get_password", return_value="fixture-key"), patch("app.main.fetch_market_context", return_value=market_fixture())]
        for mock_patch in patches:
            mock_patch.start()
            self.addCleanup(mock_patch.stop)

    def request(self, payload: dict | None = None, path: str = "/api/ai/analyze", method: str = "POST") -> tuple[int, dict]:
        """Return JSON for successful and failed real HTTP calls alike."""
        request = Request(self.url + path, method=method, data=json.dumps(self.payload if payload is None else payload).encode(), headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=10) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def test_report_preserves_text_contract_and_exposes_sources(self) -> None:
        """Capture the outbound prompt and verify returned time/source metadata over HTTP."""
        response = BytesIO(json.dumps({"choices": [{"message": {"content": "综合分析 [G5]"}}]}).encode())
        with patch("app.main.urlopen", return_value=response) as upstream:
            status, result = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(result["analysis"], "综合分析 [G5]")
        self.assertEqual(result["completionStatus"], "complete")
        self.assertEqual(result["sources"][0]["id"], "G5")
        self.assertEqual(result["warnings"], ["北向净买入无法核验"])
        self.assertEqual(result["targetSourceTime"], self.payload["sourceTime"])
        outgoing = json.loads(upstream.call_args.args[0].data)
        prompt = json.loads(outgoing["messages"][1]["content"])
        self.assertEqual(prompt["currentTarget"]["selectedStrategy"], self.payload["strategy"])
        self.assertIn("globalMarkets", prompt["crossMarketEvidence"])
        self.assertEqual(outgoing["max_tokens"], 5000)
        self.assertNotIn("reasoning_effort", outgoing)

    def test_deepseek_v4_reserves_budget_for_reasoning_and_all_sections(self) -> None:
        """Known V4 thinking models need more than the legacy total completion budget."""
        response = BytesIO(json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": "完整报告"}}]}).encode())
        with patch("app.main.get_ai_settings", return_value={"baseUrl": "https://model.example/v1", "model": "deepseek-v4.1-flash"}), patch("app.main.urlopen", return_value=response) as upstream:
            status, result = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(result["completionStatus"], "complete")
        outgoing = json.loads(upstream.call_args.args[0].data)
        self.assertEqual(outgoing["max_tokens"], 16000)
        self.assertEqual(outgoing["reasoning_effort"], "low")

    def test_truncated_prose_is_preserved_and_marked_incomplete(self) -> None:
        """A valid partial answer must remain available without masquerading as complete."""
        response = BytesIO(json.dumps({"choices": [{"finish_reason": "length", "message": {"content": "一、美股\n部分正文"}}]}).encode())
        with patch("app.main.urlopen", return_value=response):
            status, result = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(result["analysis"], "一、美股\n部分正文")
        self.assertEqual(result["completionStatus"], "truncated")
        self.assertEqual(result["sources"][0]["id"], "G5")

    def test_reasoning_only_truncation_never_exposes_reasoning_as_report(self) -> None:
        """Private reasoning cannot be substituted when no report prose was returned."""
        response = BytesIO(json.dumps({"choices": [{"finish_reason": "length", "message": {"content": "", "reasoning_content": "private-reasoning-fixture"}}]}).encode())
        with patch("app.main.urlopen", return_value=response):
            status, result = self.request()
        self.assertEqual(status, 502)
        self.assertIn("尚未生成报告正文", result["detail"])
        self.assertNotIn("private-reasoning-fixture", json.dumps(result))

    def test_empty_and_malformed_model_outputs_return_502(self) -> None:
        """An empty or structurally invalid model reply must never count as a report."""
        for payload in ({"choices": []}, {"choices": [None]}, {"choices": [{"message": None}]}, {"choices": [{"message": {"content": " "}}]},
                        {"choices": [{"message": {"content": []}}]}, {"choices": [{"finish_reason": "content_filter", "message": {"content": "未完成的报告"}}]}):
            with self.subTest(payload=payload), patch("app.main.urlopen", return_value=BytesIO(json.dumps(payload).encode())):
                status, _ = self.request()
            self.assertEqual(status, 502)

    def test_upstream_failure_redacts_key(self) -> None:
        """A provider rejection remains visible without disclosing the credential."""
        error = HTTPError("https://model.example", 401, "Unauthorized", {}, BytesIO(b'{"error":{"message":"bad fixture-key"}}'))
        with patch("app.main.urlopen", side_effect=error):
            status, result = self.request()
        self.assertEqual(status, 502)
        self.assertNotIn("fixture-key", result["detail"])

    def test_unconfigured_model_returns_409_before_collecting_market_data(self) -> None:
        """No configuration must fail before any external collection or model request."""
        with patch("app.main.get_ai_settings", return_value={}), patch("app.main.fetch_market_context") as fetch:
            status, _ = self.request()
        self.assertEqual(status, 409)
        fetch.assert_not_called()

    def test_missing_request_fields_return_422(self) -> None:
        """Pydantic rejects malformed requests before invoking the model."""
        with patch("app.main.urlopen") as upstream:
            status, _ = self.request({"symbol": "159977"})
        self.assertEqual(status, 422)
        upstream.assert_not_called()

    def test_blank_model_settings_return_422_before_any_write(self) -> None:
        """Reject empty and whitespace-only IDs before saving settings or credentials."""
        for model in ("", "   "):
            with self.subTest(model=model), patch("app.main.keyring.set_password") as save_key, patch("app.main.Path.write_text") as save_file:
                status, _ = self.request({"baseUrl": "https://model.example/v1", "model": model, "apiKey": "fixture-key"}, "/api/ai/settings", "PUT")
            self.assertEqual(status, 422)
            save_key.assert_not_called()
            save_file.assert_not_called()

    def test_nonfinite_nested_evidence_returns_422(self) -> None:
        """Invalid numeric evidence fails validation rather than the model JSON serializer."""
        self.payload["analysis"] = {"price": float("nan")}
        with patch("app.main.urlopen") as upstream:
            status, _ = self.request()
        self.assertEqual(status, 422)
        upstream.assert_not_called()


if __name__ == "__main__":
    unittest.main()
