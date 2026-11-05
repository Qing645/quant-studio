"""Verify editable rules in the engine and over real HTTP with isolated fixtures."""

import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd
import uvicorn

from app.engine import _strategy_signals, analyze_strategy, run_backtest
from app.main import app
from app.strategies import get_strategy_template, normalize_strategy_parameters


def sample_bars(minute: bool = False) -> pd.DataFrame:
    """Create a deterministic final breakout after sufficient indicator warmup."""
    close, volume = np.full(100, 10.), np.full(100, 1000.)
    close[-3:], volume[-3] = [11., 11.1, 11.2], 1300.
    return pd.DataFrame({
        "datetime": pd.date_range("2026-01-01 09:30", periods=100, freq="min" if minute else "D"),
        "open": close, "high": close + .05, "low": close - .05,
        "close": close, "volume": volume,
    })


class EngineParametersTest(unittest.TestCase):
    """Check that settings change trading behavior, rather than just labels."""

    def test_legacy_defaults_preserve_all_three_results(self) -> None:
        """Omitted settings must match the canonical defaults for every strategy type."""
        for kind in ("swing_breakout", "intraday_scalp", "trend_filter"):
            with self.subTest(kind=kind):
                template = get_strategy_template(kind)
                args = (sample_bars(kind == "intraday_scalp"), 100_000, .0003, 5,
                        template["defaultFastPeriod"], template["defaultSlowPeriod"], kind)
                self.assertEqual(run_backtest(*args), run_backtest(
                    *args, parameters=normalize_strategy_parameters(kind)))

    def test_volume_multiplier_changes_live_entry_and_backtest(self) -> None:
        """The same threshold must govern the signal and next-open execution paths."""
        bars = sample_bars()
        for multiplier, expected in ((1.2, True), (2, False)):
            with self.subTest(multiplier=multiplier):
                parameters = {"volumeMultiplier": multiplier}
                live = analyze_strategy(bars.iloc[:-2], "swing_breakout", 10, 20,
                                        "159977", "daily", parameters=parameters)
                result = run_backtest(bars, 100_000, 0, 0, 10, 20, "swing_breakout", parameters=parameters)
                self.assertEqual(live["entryCondition"], expected)
                self.assertEqual(result["openPosition"], expected)
                if expected:
                    self.assertEqual(result["openPositions"][0]["entry"], bars.iloc[-2]["datetime"].isoformat())
                else:
                    self.assertEqual([s["totalReturnPct"] for s in result["costSensitivity"]], [0, 0])

    def test_swing_atr_changes_exit_independently_of_fast_line(self) -> None:
        """ATR thresholds must change exits without triggering the two-close SMA rule."""
        close, line = np.full(80, 10.), np.full(80, 10.)
        close[68], close[70] = 10.6, 10.15
        for multiplier, expected in ((1, True), (3, False)):
            with self.subTest(multiplier=multiplier):
                _, exit_ = _strategy_signals(
                    "swing_breakout", 71, close, line, line, line, {}, slow_period=20,
                    high=close + .1, volume=np.full(80, 1000.), atr=np.full(80, .2),
                    parameters=normalize_strategy_parameters("swing_breakout", {"atrMultiplier": multiplier}))
                self.assertEqual(exit_, expected)

    def test_intraday_holding_period_and_session_close(self) -> None:
        """Use the custom holding gate while preserving mandatory completed-session exits."""
        bars = sample_bars(True).iloc[:12]

        def signals(_kind, index, *_args, **_kwargs):
            """Enter once, then keep the indicator exit active."""
            return index == 2, index >= 3

        with patch("app.engine._strategy_signals", side_effect=signals):
            for hold in (1, 5):
                result = run_backtest(bars, 100_000, 0, 0, 3, 8, "intraday_scalp", parameters={"minHoldBars": hold})
                self.assertEqual(result["trades"][0]["exit"], bars.iloc[2 + hold]["datetime"].isoformat())
            forced = run_backtest(bars, 100_000, 0, 0, 3, 8, "intraday_scalp",
                                  parameters={"minHoldBars": 60}, session_complete=True)
            self.assertFalse(forced["openPosition"])

    def test_intraday_atr_multiplier_changes_exit(self) -> None:
        """ATR exits must use each instance's risk threshold."""
        bars = sample_bars(True).iloc[:30].copy()
        bars.loc[25:, ["open", "close"]] = [[10.5, 10.5]] + [[10.2, 10.2]] * 4
        bars["high"], bars["low"] = bars["close"] + .05, bars["close"] - .05

        def signals(_kind, index, *_args, **_kwargs):
            """Enter before the pullback and let only the ATR condition exit."""
            return index == 25, False

        with patch("app.engine._strategy_signals", side_effect=signals), \
                patch("app.engine.talib.ATR", return_value=np.full(len(bars), .2)):
            tight = run_backtest(bars, 100_000, 0, 0, 3, 8, "intraday_scalp",
                                 parameters={"minHoldBars": 1, "atrMultiplier": .5})
            wide = run_backtest(bars, 100_000, 0, 0, 3, 8, "intraday_scalp",
                                parameters={"minHoldBars": 1, "atrMultiplier": 3})
        self.assertEqual(tight["tradeCount"], 1)
        self.assertTrue(wide["openPosition"])

    def test_target_volatility_changes_exposure_and_live_context(self) -> None:
        """Risk budgets must influence entry/monthly sizing and be available to live AI."""
        close = np.cumprod(1 + np.random.default_rng(101).normal(.0008, .025, 500))
        bars = pd.DataFrame({
            "datetime": pd.date_range("2024-01-01", periods=500, freq="D"),
            "open": close, "high": close * 1.01, "low": close * .99,
            "close": close, "volume": np.full(500, 1000.),
        })
        low = run_backtest(bars, 100_000, .0003, 5, 20, 60, parameters={"targetVolatilityPct": 5})
        high = run_backtest(bars, 100_000, .0003, 5, 20, 60, parameters={"targetVolatilityPct": 40})
        self.assertLess(low["exposurePct"], high["exposurePct"])
        self.assertGreater(low["rebalanceCount"], 0)
        self.assertEqual(low["positionSizingTargetVolatilityPct"], 5)
        live = analyze_strategy(bars, "trend_filter", 20, 60, "159977", "daily", parameters={"targetVolatilityPct": 5})
        self.assertEqual(live["parameters"], {"targetVolatilityPct": 5})
        self.assertIn("5%", live["message"])

    def test_invalid_parameters(self) -> None:
        """Reject unknown keys, wrong types, invalid ranges and nonfinite numbers."""
        for kind, parameters in (
            ("trend_filter", {"volumeMultiplier": 1.2}),
            ("trend_filter", {"targetVolatilityPct": 0}),
            ("swing_breakout", {"atrMultiplier": float("nan")}),
            ("swing_breakout", {"atrMultiplier": True}),
            ("swing_breakout", {"volumeMultiplier": "1.2"}),
            ("intraday_scalp", {"minHoldBars": 2.5}),
            ("intraday_scalp", {"minHoldBars": 10 ** 1000}),
        ):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                normalize_strategy_parameters(kind, parameters)


class LocalStrategyAPITest(unittest.TestCase):
    """Verify real HTTP requests using temporary files and synthetic market data."""

    @classmethod
    def setUpClass(cls) -> None:
        """Run a temporary local service with a bounded readiness check."""
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
            cls.tearDownClass()
            raise RuntimeError("临时测试服务未就绪")

    @classmethod
    def tearDownClass(cls) -> None:
        """Stop the temporary service when HTTP verification finishes."""
        cls.server.should_exit = True
        cls.thread.join(timeout=5)
        cls.sock.close()

    def setUp(self) -> None:
        """Isolate saved data and all public-market requests from user state."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = Path(temporary.name) / "strategies.json"
        self.store.write_text("[]", encoding="utf-8")
        for target, value in (
            ("app.main.STRATEGIES_FILE", self.store),
            ("app.main.fetch_daily_history", lambda *_args: (sample_bars(), {})),
            ("app.main.fetch_bars", lambda _code, period: (sample_bars(period in {"intraday", "five_day"}),
                {"marketOpen": False, "sourceTime": "2026-04-10", "sessionComplete": False})),
        ):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def request(self, path: str, payload: dict | None = None) -> tuple[int, object]:
        """Read structured success or error responses from the local API."""
        request = Request(self.url + path, data=json.dumps(payload).encode() if payload is not None else None,
                          headers={"Content-Type": "application/json"})
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)



class StrategyParametersHTTPTest(LocalStrategyAPITest):
    """Check built-in parameters against the shared temporary HTTP service."""

    def test_roundtrip_saved_instances(self) -> None:
        """Save all three custom instances and retain parameters across revisions."""
        for kind, parameters in (
            ("swing_breakout", {"volumeMultiplier": 1.7, "atrMultiplier": 2.8}),
            ("intraday_scalp", {"minHoldBars": 7, "atrMultiplier": 2.2}),
            ("trend_filter", {"targetVolatilityPct": 12}),
        ):
            with self.subTest(kind=kind):
                status, saved = self.request("/api/strategies", {"name": "测试实例", "strategyType": kind,
                    "fastPeriod": 8, "slowPeriod": 30, "parameters": parameters})
                self.assertEqual(status, 200)
                self.assertEqual(saved["parameters"], parameters)
                status, revised = self.request("/api/strategies", {**saved, "name": "修改名称"})
                self.assertEqual(status, 200)
                self.assertEqual(revised["version"], 2)
                self.assertEqual(revised["parameters"], parameters)
        status, items = self.request("/api/strategies")
        self.assertEqual(status, 200)
        self.assertEqual(len(items), 3)

    def test_legacy_read_does_not_rewrite_data(self) -> None:
        """Return defaults for old records without an implicit file migration."""
        original = json.dumps([{"id": "old", "strategyType": "intraday_scalp"}])
        self.store.write_text(original, encoding="utf-8")
        status, items = self.request("/api/strategies")
        self.assertEqual(status, 200)
        self.assertEqual(items[0]["parameters"], {"minHoldBars": 3, "atrMultiplier": 1.5})
        self.assertEqual(self.store.read_text(encoding="utf-8"), original)
        self.assertEqual(self.request("/api/strategies", {"name": "新实例"})[0], 200)
        self.assertEqual(json.loads(self.store.read_text())[-1], json.loads(original)[0])

    def test_live_and_backtest_parameter_propagation(self) -> None:
        """Both HTTP calculation paths must pass custom rules into the actual engine."""
        for kind, parameters, period in (
            ("swing_breakout", {"volumeMultiplier": 2, "atrMultiplier": 3}, "daily"),
            ("intraday_scalp", {"minHoldBars": 6, "atrMultiplier": 2.5}, "intraday"),
            ("trend_filter", {"targetVolatilityPct": 8}, "daily"),
        ):
            with self.subTest(kind=kind):
                query = urlencode({"strategyType": kind, "timeframe": period, "parameters": json.dumps(parameters)})
                status, live = self.request("/api/market?" + query)
                self.assertEqual(status, 200)
                self.assertEqual(live["strategy"]["parameters"], parameters)
                status, historical = self.request("/api/backtest", {"strategyType": kind,
                    "timeframe": period, "parameters": parameters})
                self.assertEqual(status, 200)
                self.assertEqual(historical["parameters"], parameters)

    def test_invalid_requests_return_422_without_saving(self) -> None:
        """Validate ranges, type mismatches, malformed JSON and incompatible keys."""
        for parameters in ({"minHoldBars": 0}, {"minHoldBars": 1.5}, {"atrMultiplier": "2"},
                           {"targetVolatilityPct": 20}, {"atrMultiplier": True}):
            with self.subTest(parameters=parameters):
                payload = {"strategyType": "intraday_scalp", "parameters": parameters}
                for path in ("/api/strategies", "/api/backtest"):
                    self.assertEqual(self.request(path, {**payload, "name": "不能保存"})[0], 422)
                query = urlencode({"strategyType": "intraday_scalp", "parameters": json.dumps(parameters)})
                self.assertEqual(self.request("/api/market?" + query)[0], 422)
        for raw in ("null", "[]", "not-json"):
            self.assertEqual(self.request("/api/market?" + urlencode({"parameters": raw}))[0], 422)
        self.assertEqual(self.request("/api/strategies", {"name": "坏周期", "fastPeriod": 60, "slowPeriod": 20})[0], 422)
        self.assertEqual(self.store.read_text(), "[]")
