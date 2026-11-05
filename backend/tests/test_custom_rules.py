"""Behavior, causality and HTTP tests for independent user-authored strategies."""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

import numpy as np
import pandas as pd
from pydantic import ValidationError

from app.custom_rules import CustomRules, RuleEvaluator, completed_custom_bars, get_rule_options
from app.engine import analyze_strategy, run_backtest
from tests.test_strategy_parameters import LocalStrategyAPITest, sample_bars


def operand(kind, **fields):
    """Create an explicit test reference without hidden algorithm parameters."""
    return {"kind": kind, "offset": 0, "multiplier": 1, **fields}


def condition(left, operator, right):
    """Build one comparison in the editor's contract."""
    return {"left": left, "operator": operator, "right": right}


def rules(**changes):
    """Define price-level entries/exits independently of all built-in algorithms."""
    return {
        "symbol": "159977", "timeframe": "daily",
        "entry": {"match": "all", "conditions": [condition(operand("close"), "gt", operand("number", value=10.5))]},
        "exit": {"match": "all", "conditions": [condition(operand("close"), "lt", operand("number", value=9.5))]},
        "positionPct": 40, "minHoldBars": 1, "stopLossPct": 0, "takeProfitPct": 0,
        "trailingStopPct": 0, "closeAtSessionEnd": False, **changes,
    }


def bars(prices, minute=False, opens=None):
    """Build valid candles for exact signal timing and execution-price assertions."""
    close = np.asarray(prices, dtype=float)
    opening = close if opens is None else np.asarray(opens, dtype=float)
    return pd.DataFrame({
        "datetime": pd.date_range("2026-01-01 09:30", periods=len(close), freq="min" if minute else "D"),
        "open": opening, "high": np.maximum(opening, close) + .1, "low": np.minimum(opening, close) - .1,
        "close": close, "volume": np.full(len(close), 1000.),
    })


def backtest(data, definition, fee=0):
    """Execute a custom definition without specifying any built-in settings."""
    return run_backtest(data, 100_000, fee, 0, strategy_type="custom", rules=definition)


class CustomRulesTest(unittest.TestCase):
    """Check shared computation, historical causality, risk and position semantics."""

    def test_next_open_costs_and_partial_position(self):
        """Use next opens and configured cash fraction with both transaction costs charged."""
        data = bars([10, 11, 12, 9, 9], opens=[10, 10.2, 11.5, 11, 8.8])
        result = backtest(data, rules(), .001)
        trade = result["trades"][0]
        self.assertEqual(trade["entry"], data.iloc[2]["datetime"].isoformat())
        self.assertEqual(trade["exit"], data.iloc[4]["datetime"].isoformat())
        self.assertAlmostEqual(trade["entryPrice"], 11.5 * 1.001)
        self.assertAlmostEqual(trade["exitPrice"], 8.8 * .999)
        self.assertAlmostEqual(trade["pnl"], 40_000 / (11.5 * 1.001) * 8.8 * .999 - 40_000)
        self.assertAlmostEqual(result["finalEquity"], 100_000 + trade["pnl"])
        self.assertNotIn("parameters", result)

    def test_no_builtin_confirmation_warmup(self):
        """A raw first-bar signal can execute on the second bar."""
        data = bars([11, 12, 12])
        result = backtest(data, rules())
        self.assertEqual(result["openPositions"][0]["entry"], data.iloc[1]["datetime"].isoformat())

    def test_all_any_and_crossing(self):
        """Group semantics change signals, while a crossing triggers only at the crossing bar."""
        definition = rules()
        definition["entry"]["conditions"].append(condition(operand("volume"), "gt", operand("number", value=2000)))
        data = bars([10, 11, 12])
        self.assertFalse(RuleEvaluator(data, CustomRules.model_validate(definition)).entry[-1])
        definition["entry"]["match"] = "any"
        self.assertTrue(RuleEvaluator(data, CustomRules.model_validate(definition)).entry[-1])
        definition = rules()
        definition["entry"]["conditions"][0]["operator"] = "crosses_above"
        evaluator = RuleEvaluator(bars([10, 11, 12, 10, 11]), CustomRules.model_validate(definition))
        np.testing.assert_array_equal(evaluator.entry, [False, True, False, False, True])
        self.assertFalse(evaluator.entry_ready[0])

    def test_prior_range_breakout_and_volume_multiplier(self):
        """Lagging the range excludes the signal bar and permits a real price breakout."""
        definition = rules()
        definition["entry"]["conditions"] = [
            condition(operand("close"), "gt", operand("highest", period=20, offset=1)),
            condition(operand("volume"), "gt", operand("volume_sma", period=20, offset=1, multiplier=1.2)),
        ]
        data = sample_bars().iloc[:-2]
        self.assertTrue(RuleEvaluator(data, CustomRules.model_validate(definition)).entry[-1])
        definition["entry"]["conditions"][0]["right"]["offset"] = 0
        self.assertFalse(RuleEvaluator(data, CustomRules.model_validate(definition)).entry[-1])

    def test_all_indicators_preserve_prefix_when_future_is_added(self):
        """Every supported indicator must use causal trailing windows and nonnegative lag."""
        data = bars(10 + np.arange(100) * .02 + np.sin(np.arange(100) / 4))
        for indicator in get_rule_options()["indicators"]:
            with self.subTest(kind=indicator["value"]):
                reference = operand(indicator["value"], offset=2)
                if indicator["period"]:
                    reference["period"] = 5
                if indicator["value"] == "number":
                    reference.update(value=10, offset=0)
                definition = rules(entry={"match": "all", "conditions": [condition(reference, "gt", operand("number", value=10))]})
                full = RuleEvaluator(data, CustomRules.model_validate(definition))
                prefix = RuleEvaluator(data.iloc[:70], CustomRules.model_validate(definition))
                np.testing.assert_array_equal(full.entry[:70], prefix.entry)
                np.testing.assert_array_equal(full.entry_ready[:70], prefix.entry_ready)

    def test_changed_future_cannot_change_prior_equity_and_trades(self):
        """Compare prior account values and completed executions after changing unseen prices."""
        data = bars(10 + np.sin(np.arange(100) / 3))
        result = backtest(data, rules(), .001)
        future = data.copy()
        future.loc[70:, ["open", "close", "high", "low"]] *= 3
        changed = backtest(future, rules(), .001)
        self.assertEqual(result["equityCurve"][:70], changed["equityCurve"][:70])
        cutoff = data.iloc[70]["datetime"].isoformat()
        self.assertEqual([t for t in result["trades"] if t["exit"] < cutoff], [t for t in changed["trades"] if t["exit"] < cutoff])

    def test_risk_controls_bypass_holding_gate(self):
        """Risk conditions confirm on closes and execute next open without waiting for the gate."""
        for setting, prices, exit_index in (("stopLossPct", [11, 11, 9, 8], 3),
                                           ("takeProfitPct", [11, 11, 13, 14], 3),
                                           ("trailingStopPct", [11, 11, 13, 11, 10], 4)):
            with self.subTest(setting=setting):
                data = bars(prices)
                result = backtest(data, rules(minHoldBars=100, **{setting: 5}))
                self.assertEqual(result["tradeCount"], 1)
                self.assertEqual(result["trades"][0]["exit"], data.iloc[exit_index]["datetime"].isoformat())

    def test_minimum_holding_period_gates_only_condition_exits(self):
        """Condition exits wait the configured number of held bars before next-open execution."""
        data = bars([11, 9, 9, 9, 9, 9])
        for hold, expected in ((1, 2), (4, 5)):
            with self.subTest(hold=hold):
                result = backtest(data, rules(minHoldBars=hold))
                self.assertEqual(result["trades"][0]["exit"], data.iloc[expected]["datetime"].isoformat())

    def test_conflicts_scope_and_warmup(self):
        """Expose conflicts and unsuitable data explicitly, without fabricating entry decisions."""
        definition = rules()
        definition["exit"] = deepcopy(definition["entry"])
        data = bars([11, 11, 11])
        live = analyze_strategy(data, "custom", 20, 60, "159977", "daily", rules=definition)
        self.assertIn("同时满足", live["signal"])
        self.assertFalse(backtest(data, definition)["openPosition"])
        self.assertFalse(analyze_strategy(data, "custom", 20, 60, "159915", "daily", rules=definition)["supported"])
        self.assertFalse(analyze_strategy(data, "custom", 20, 60, "159977", "intraday", rules=definition)["supported"])
        definition["entry"]["conditions"][0]["left"] = operand("sma", period=60)
        self.assertEqual(analyze_strategy(data, "custom", 20, 60, "159977", "daily", rules=definition)["signal"], "数据不足")

    def test_session_policy_partial_and_complete_samples(self):
        """Preserve partial-day positions; flatten a confirmed complete session when configured."""
        data = bars([11, 11, 11, 11], minute=True)
        definition = rules(timeframe="intraday", closeAtSessionEnd=True, minHoldBars=100)
        self.assertTrue(backtest(data, definition)["openPosition"])
        complete = run_backtest(data, 100_000, 0, 0, strategy_type="custom", rules=definition, session_complete=True)
        self.assertFalse(complete["openPosition"])
        self.assertIsNone(complete["sharpe"])
        self.assertEqual(complete["trades"][0]["exit"], data.iloc[-1]["datetime"].isoformat())

    def test_unfinished_candles_are_excluded_until_their_own_period_ends(self):
        """Week/month signals must not become final at the end of an intermediate trading day."""
        data = bars([10, 11])
        for timeframe, last, before, after in (
            ("intraday", "2026-11-05 10:30", "2026-11-05 10:30:45", "2026-11-05 10:31"),
            ("daily", "2026-11-05", "2026-11-05 14:59", "2026-11-05 15:00"),
            ("weekly", "2026-11-05", "2026-11-05 16:00", "2026-11-06 15:00"),
            ("monthly", "2026-11-05", "2026-11-06 16:00", "2026-11-30 15:00"),
        ):
            with self.subTest(timeframe=timeframe):
                sample = data.copy()
                sample.loc[1, "datetime"] = pd.Timestamp(last)
                prior, unfinished = completed_custom_bars(sample, timeframe, now=pd.Timestamp(before))
                self.assertTrue(unfinished)
                self.assertEqual(len(prior), 1)
                complete, unfinished = completed_custom_bars(sample, timeframe, now=pd.Timestamp(after))
                self.assertFalse(unfinished)
                self.assertEqual(len(complete), 2)

    def test_reject_invalid_rules(self):
        """Refuse missing fields, negative lags, executable code, empty groups and bad risk settings."""
        definitions = [rules(positionPct=101), rules(closeAtSessionEnd=True), rules(minHoldBars=1.5),
                       rules(entry={"match": "all", "conditions": []}),
                       rules(entry={"match": "all", "conditions": rules()["entry"]["conditions"] * 13})]
        for reference in (operand("sma"), operand("close", offset=-1), operand("rsi", period=1),
                          operand("number", value=float("inf")), operand("python", value="__import__('os')")):
            definitions.append(rules(entry={"match": "all", "conditions": [condition(reference, "gt", operand("number", value=10))]}))
        for definition in definitions:
            with self.subTest(definition=definition), self.assertRaises(ValidationError):
                CustomRules.model_validate(definition)


class CustomRulesHTTPTest(LocalStrategyAPITest):
    """Check real HTTP contracts without touching user strategies or external market providers."""

    def test_save_read_edit_backtest_and_builtin_isolation(self):
        """Persist independent rules, revise them and execute the actual saved definition."""
        _, builtin = self.request("/api/strategies", {"name": "原策略"})
        status, saved = self.request("/api/strategies", {"name": "我的策略", "strategyType": "custom", "rules": rules()})
        self.assertEqual(status, 200)
        self.assertEqual(saved["rules"], rules())
        for key in ("fastPeriod", "slowPeriod", "parameters"):
            self.assertNotIn(key, saved)
        saved["rules"]["positionPct"] = 25
        status, edited = self.request("/api/strategies", saved)
        self.assertEqual((status, edited["version"]), (200, 2))
        self.assertEqual(self.request("/api/strategies")[1][0]["rules"]["positionPct"], 25)
        self.assertEqual(json.loads(self.store.read_text())[1], builtin)
        status, result = self.request("/api/backtest", {"strategyType": "custom", "symbol": "159977", "timeframe": "daily", "rules": edited["rules"]})
        self.assertEqual(status, 200)
        self.assertEqual(result["rules"], edited["rules"])
        self.assertAlmostEqual(result["openPositions"][0]["entryCapital"], 25_000)

    def test_live_rules_scope_and_ai_context(self):
        """Pass authored rules to realtime evaluation and keep them in the AI context object."""
        query = urlencode({"strategyType": "custom", "rules": json.dumps(rules())})
        status, live = self.request("/api/market?" + query)
        self.assertEqual(status, 200)
        self.assertTrue(live["strategy"]["entryCondition"])
        self.assertEqual(live["strategy"]["rules"], rules())
        self.assertNotIn("parameters", live["strategy"])
        status, mismatch = self.request("/api/market?" + query + "&timeframe=weekly")
        self.assertEqual(status, 200)
        self.assertFalse(mismatch["strategy"]["supported"])

    def test_live_excludes_unfinished_minute(self):
        """Do not confirm an entry using the unfinished last minute."""
        data = bars([10] * 99 + [11], minute=True)
        query = urlencode({"strategyType": "custom", "timeframe": "intraday", "rules": json.dumps(rules(timeframe="intraday"))})
        def market_data(_symbol, period):
            """Keep the five-day indicator history separate from the actual current-session rules."""
            return (sample_bars(True) if period == "five_day" else data, {"marketOpen": True, "sourceTime": "2026-01-01"})

        with patch("app.main.fetch_bars", side_effect=market_data), patch(
            "app.main.completed_custom_bars", side_effect=lambda data, timeframe: completed_custom_bars(
                data, timeframe, now=pd.Timestamp("2026-01-01 11:09:30"))
        ):
            status, live = self.request("/api/market?" + query)
        self.assertEqual(status, 200)
        self.assertFalse(live["strategy"]["entryCondition"])
        self.assertEqual(live["strategy"]["asOf"], data.iloc[-2]["datetime"].isoformat())

    def test_invalid_and_mixed_contracts_return_422_without_saving(self):
        """Reject invalid definitions and mixing custom rules with built-in parameters."""
        for payload in ({"strategyType": "custom"},
                        {"strategyType": "custom", "rules": rules(entry={"match": "all", "conditions": []})},
                        {"strategyType": "custom", "rules": rules(), "fastPeriod": 10},
                        {"strategyType": "custom", "rules": rules(), "parameters": {"atrMultiplier": 2}},
                        {"strategyType": "trend_filter", "rules": rules()}):
            with self.subTest(payload=payload):
                for path in ("/api/strategies", "/api/backtest"):
                    self.assertEqual(self.request(path, {**payload, "name": "非法"})[0], 422)
        for raw in ("null", "[]", "bad-json"):
            self.assertEqual(self.request("/api/market?" + urlencode({"strategyType": "custom", "rules": raw}))[0], 422)
        self.assertEqual(self.request("/api/backtest", {"strategyType": "custom", "rules": rules(), "symbol": "159915"})[0], 422)
        self.assertEqual(self.store.read_text(), "[]")

    def test_catalog_separate_from_presets(self):
        """Publishing editable vocabulary must not create another built-in strategy."""
        status, options = self.request("/api/strategy-rule-options")
        self.assertEqual(status, 200)
        self.assertIn("highest", {item["value"] for item in options["indicators"]})
        self.assertEqual(len(self.request("/api/strategy-templates")[1]), 3)
