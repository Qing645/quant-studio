"""Inventory, causal execution and API contracts for the sell-first T+1 base-position strategy."""

import json
from urllib.parse import urlencode
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from app.engine import analyze_strategy, run_backtest
from app.intraday import BaseTAccount, base_t_condition, decision_bars, session_indicators
from tests.test_strategy_parameters import LocalStrategyAPITest


def minute_bars(start: str = "2026-11-02 10:00", count: int = 35) -> pd.DataFrame:
    """Provide a sell premium followed by a stabilizing lower-price buyback opportunity."""
    close = np.full(count, 10.5)
    close[6:] = 10.
    return pd.DataFrame({"datetime": pd.date_range(start, periods=count, freq="min"),
        "open": close, "high": close + .02, "low": close - .02, "close": close,
        "volume": np.full(count, 1000.)})


class BaseTInventoryTest(unittest.TestCase):
    """Use scripted price conditions to isolate real execution, accounting and settlement behavior."""

    def run_fixture(self, bars: pd.DataFrame | None = None, *, sells=(2,), recoveries=(6,),
                    parameters=None, fee=.0003, slip=5, complete=False) -> dict:
        """Control only observations; cash, lots, costs, matching and T+1 execution remain real."""
        bars = minute_bars() if bars is None else bars
        indicators = {name: np.full(len(bars), value) for name, value in (("fast", 10.3), ("slow", 10.2), ("atr", .05), ("vwap", 10.))}

        def condition(_close, _indicators, current, _multiplier, _cost):
            """Expose independently scheduled sell and recovery observations."""
            return current in sells, current in recoveries, .1

        with patch("app.intraday.session_indicators", return_value=indicators), patch("app.intraday.base_t_condition", side_effect=condition):
            return run_backtest(bars, 100_000, fee, slip, 3, 8, "intraday_scalp", parameters=parameters,
                                session_complete=complete, _include_cost_sensitivity=False)

    def test_same_base_benchmark_and_accounting_include_both_legs(self) -> None:
        """Attribute total PnL exactly to baseline price movement plus net T contribution."""
        result = self.run_fixture()
        sell, buy = result["executions"]
        self.assertEqual([fill["side"] for fill in result["executions"]], ["sell", "buy"])
        self.assertEqual(sell["time"], "2026-11-02T10:03:00")
        self.assertEqual(buy["time"], "2026-11-02T10:07:00")
        quantity = sell["shares"]
        self.assertEqual(result["trades"][0]["direction"], "sell_buy")
        self.assertAlmostEqual(result["trades"][0]["pnl"], quantity * (10.5 * .9992 - 10 * 1.0008))
        self.assertAlmostEqual(result["trades"][0]["transactionCost"], quantity * (10.5 + 10) * .0008)
        self.assertAlmostEqual(result["finalEquity"] - result["initialCapital"], result["baseT"]["basePnl"] + result["baseT"]["tPnl"])
        self.assertAlmostEqual(result["baseT"]["tPnl"], result["baseT"]["closedTPnl"])
        self.assertEqual(result["benchmark"]["name"], "同等初始底仓持有")

    def test_bought_shares_cannot_be_recycled_same_day(self) -> None:
        """Repeated new setups cannot sell locked buybacks after the old inventory is consumed."""
        bars = minute_bars()
        bars[["open", "close", "high", "low"]] = [10.5, 10., 10.52, 9.98]
        result = self.run_fixture(bars, sells=(2, 10, 18, 26), recoveries=(6, 14, 22, 30))
        self.assertEqual(result["tradeCount"], 2)
        self.assertEqual(sum(fill["shares"] for fill in result["executions"] if fill["side"] == "sell"), 5000)
        self.assertEqual(result["baseT"]["lockedShares"], 5000)
        self.assertEqual(result["baseT"]["sellableShares"], 0)
        self.assertTrue(all(fill["shares"] % 100 == 0 for fill in result["executions"]))

    def test_lunch_does_not_unlock_but_next_session_does(self) -> None:
        """The date transition, rather than a time gap, determines settlement availability."""
        bars = minute_bars(count=35)
        bars[["open", "close", "high", "low"]] = [10.5, 10., 10.52, 9.98]
        bars.loc[16:24, "datetime"] = pd.date_range("2026-11-02 13:00", periods=9, freq="min")
        bars.loc[25:, "datetime"] = pd.date_range("2026-11-03 10:00", periods=10, freq="min")
        result = self.run_fixture(bars, sells=(2, 10, 18, 27), recoveries=(6, 14, 22, 31))
        sells = [fill for fill in result["executions"] if fill["side"] == "sell"]
        self.assertEqual([fill["time"][11:16] for fill in sells], ["10:03", "10:11", "10:03"])
        self.assertEqual(result["baseT"]["lockedShares"], 2500)

    def test_persistent_condition_does_not_reenter_without_new_setup(self) -> None:
        """A continually true label must not retrigger immediately after a buyback."""
        bars = minute_bars()
        bars[["open", "close", "high", "low"]] = [10.5, 10., 10.52, 9.98]
        result = self.run_fixture(bars, sells=tuple(range(2, 35)), recoveries=tuple(range(6, 35)))
        self.assertEqual(result["tradeCount"], 1)

    def test_partial_session_keeps_pending_t_marked_to_market(self) -> None:
        """A data cutoff cannot manufacture a closing fill, even when marked session-complete."""
        bars = minute_bars(count=6)
        for complete in (False, True):
            result = self.run_fixture(bars, recoveries=(), complete=complete)
            self.assertEqual(len(result["executions"]), 1)
            self.assertGreater(result["baseT"]["pendingBuybackShares"], 0)
            self.assertEqual(result["tradeCount"], 0)
            self.assertAlmostEqual(result["baseT"]["tPnl"], -result["baseT"]["transactionCost"])

    def test_scheduled_restoration_buys_instead_of_flattening_base(self) -> None:
        """At 14:55 restore sold shares, retaining the overnight base and locking the buyback."""
        bars = minute_bars("2026-11-02 14:40", count=20)
        result = self.run_fixture(bars, recoveries=(), parameters={"minHoldBars": 60}, complete=True)
        self.assertEqual(result["trades"][0]["exit"], "2026-11-02T14:55:00")
        self.assertTrue(result["openPosition"])
        self.assertEqual(result["baseT"]["initialShares"], result["baseT"]["currentShares"])
        self.assertEqual(result["baseT"]["pendingBuybackShares"], 0)

    def test_upward_protection_bypasses_wait_and_can_lose_money(self) -> None:
        """A missed rally is an honest negative T contribution; do not defer protection to improve win rate."""
        bars = minute_bars(count=10)
        bars.loc[4:, ["open", "close", "high", "low"]] = [10.8, 10.8, 10.82, 10.78]
        result = self.run_fixture(bars, recoveries=(), parameters={"minHoldBars": 60})
        self.assertLess(result["trades"][0]["pnl"], 0)
        self.assertEqual(result["trades"][0]["exitReason"], "上涨保护买回")

    def test_gap_does_not_force_an_unfunded_restoration(self) -> None:
        """A full-base account cannot borrow cash to buy back a gap at the old sale price."""
        bars = minute_bars("2026-11-02 14:40", count=20)
        bars.loc[15:, ["open", "close", "high", "low"]] = [20., 20., 20.02, 19.98]
        result = self.run_fixture(bars, recoveries=(), parameters={"basePositionPct": 100, "tPositionPct": 100})
        self.assertGreater(result["baseT"]["pendingBuybackShares"], 0)
        self.assertGreaterEqual(result["baseT"]["cash"], 0)
        self.assertTrue(all(fill["cash"] >= -1e-8 for fill in result["executions"]))

    def test_next_day_restores_unfinished_t_at_first_observed_open(self) -> None:
        """Missing previous closing quotes leaves a real deficit until the next observed execution."""
        bars = minute_bars(count=8)
        bars.loc[6:, "datetime"] = pd.date_range("2026-11-03 09:30", periods=2, freq="min")
        result = self.run_fixture(bars, recoveries=())
        self.assertEqual(result["trades"][0]["exit"], "2026-11-03T09:30:00")
        self.assertEqual(result["trades"][0]["exitReason"], "次日补回未完成底仓")

    def test_base_fraction_changes_exposure_and_benchmark_equally(self) -> None:
        """Lower capital exposure must lower the reference risk as well as the account risk."""
        low = self.run_fixture(parameters={"basePositionPct": 25})
        high = self.run_fixture(parameters={"basePositionPct": 75})
        self.assertLess(low["baseT"]["initialShares"], high["baseT"]["initialShares"])
        self.assertLess(low["exposurePct"], high["exposurePct"])
        self.assertGreater(low["benchmark"]["totalReturnPct"], high["benchmark"]["totalReturnPct"])

    def test_minimum_wait_gates_pullback_but_not_scheduled_restore(self) -> None:
        """Editable waiting roots affect actual buyback timing independently of protective exits."""
        short = self.run_fixture(recoveries=tuple(range(6, 35)), parameters={"minHoldBars": 1})
        long = self.run_fixture(recoveries=tuple(range(6, 35)), parameters={"minHoldBars": 10})
        self.assertLess(short["trades"][0]["exit"], long["trades"][0]["exit"])

    def test_invalid_inventory_cannot_be_sold_directly(self) -> None:
        """The account rejects overselling and invalid lots even outside the normal signal loop."""
        account = BaseTAccount(100., 200, 100, locked=100)
        for quantity in (200, 1, -100):
            with self.assertRaises(ValueError):
                account.execute("sell", quantity, 10., 0., "2026-11-02T10:00", "test")


class BaseTSignalsTest(unittest.TestCase):
    """Validate signal thresholds, settlement-independent live observations and prefix invariance."""

    def test_cost_floor_and_atr_multiplier_affect_price_condition(self) -> None:
        """Both trading costs and the editable ATR multiple can veto a narrow premium."""
        close = np.array([10.21, 10.22, 10.21])
        indicators = {"fast": np.array([10.24, 10.24, 10.23]), "slow": np.full(3, 10.25),
                      "vwap": np.full(3, 10.), "atr": np.full(3, .1)}
        self.assertTrue(base_t_condition(close, indicators, 2, 1., 0.)[0])
        self.assertFalse(base_t_condition(close, indicators, 2, 3., 0.)[0])
        self.assertFalse(base_t_condition(close, indicators, 2, 1., .02)[0])

    def test_live_condition_is_not_a_position_aware_sell_instruction(self) -> None:
        """Realtime messages must explain that old inventory and the prior sale price are unknown."""
        bars = minute_bars()
        result = analyze_strategy(bars, "intraday_scalp", 3, 8, "159977", "intraday")
        self.assertEqual(result["action"], "observe")
        self.assertEqual(result["tradingMode"], "base_t")
        self.assertIn("无真实可卖份额", result["message"])
        self.assertIn("当天买回份额不可再次卖出", result["message"])

    def test_one_bar_pullback_does_not_sell_a_continuing_uptrend(self) -> None:
        """Preserve the base when a minor decline has not confirmed an EMA trend rollover."""
        close = np.array([10.22, 10.23, 10.22])
        indicators = {"fast": np.array([10.22, 10.22, 10.21]), "slow": np.full(3, 10.1),
                      "vwap": np.full(3, 10.), "atr": np.full(3, .03)}
        self.assertFalse(base_t_condition(close, indicators, 2, 1.5, .0008)[0])

    def test_indicators_restart_warmup_on_the_next_session(self) -> None:
        """Yesterday's EMA and ATR cannot initialize a false sell condition at today's open."""
        bars = minute_bars()
        tomorrow = minute_bars("2026-11-03 09:30", count=4)
        values = session_indicators(pd.concat([bars, tomorrow], ignore_index=True), 3, 8)
        self.assertTrue(np.isnan(values["slow"][-4:]).all())
        self.assertTrue(np.isnan(values["atr"][-4:]).all())

    def test_ohlc_zero_volume_does_not_invent_vwap(self) -> None:
        """No tradeable weighted average exists without observed positive session volume."""
        bars = minute_bars()
        bars["volume"] = 0.
        self.assertTrue(np.isnan(session_indicators(bars, 3, 8)["vwap"]).all())
        self.assertEqual(analyze_strategy(bars, "intraday_scalp", 3, 8, "159977", "intraday")["signal"], "数据不足")

    def test_later_prices_do_not_change_earlier_equity_or_execution(self) -> None:
        """Appending a rally/crash must leave every earlier fill and valuation unchanged."""
        bars = minute_bars(count=160)
        close = 10 + np.arange(160) * .001 + .1 * np.sin(np.arange(160) / 4)
        bars[["open", "close"]] = np.column_stack((close, close))
        bars["high"], bars["low"] = close + .002, close - .002
        prefix = bars.iloc[:90].copy()
        full = run_backtest(bars, 100_000, .0003, 5, 3, 8, "intraday_scalp", _include_cost_sensitivity=False)
        short = run_backtest(prefix, 100_000, .0003, 5, 3, 8, "intraday_scalp", _include_cost_sensitivity=False)
        self.assertEqual(short["equityCurve"], full["equityCurve"][:90])
        cutoff = prefix.datetime.iloc[-1].isoformat()
        self.assertEqual(short["executions"], [fill for fill in full["executions"] if fill["time"] <= cutoff])


class BaseTHTTPTest(LocalStrategyAPITest):
    """Verify real API success and invalid configuration paths with isolated public-data fixtures."""

    def test_backtest_uses_dated_five_day_feed_and_exposes_attribution(self) -> None:
        """A T+1 simulation returns the inventory ledger and the matching initial-base benchmark."""
        with patch("app.main.fetch_bars", return_value=(minute_bars(), {"sampled": True})) as fetch:
            status, result = self.request("/api/backtest", {"symbol": "159977", "timeframe": "intraday",
                "strategyType": "intraday_scalp", "initialCapital": 10000, "fastPeriod": 3, "slowPeriod": 8})
        self.assertEqual(status, 200)
        fetch.assert_called_once_with("159977", "five_day")
        self.assertIn("baseT", result)
        self.assertEqual(result["benchmark"]["name"], "同等初始底仓持有")
        self.assertIsNone(result["annualizedReturnPct"])
        self.assertIsNone(result["sharpe"])

    def test_invalid_capital_lot_and_parameters_return_422(self) -> None:
        """Reject unusable lot sizes and unknown configuration rather than faking available shares."""
        payload = {"symbol": "159977", "timeframe": "intraday", "strategyType": "intraday_scalp",
                   "initialCapital": 1000, "parameters": {"basePositionPct": 10, "tPositionPct": 10}}
        self.assertEqual(self.request("/api/backtest", payload)[0], 422)
        self.assertEqual(self.request("/api/strategies", {"name": "bad", "strategyType": "intraday_scalp",
                                                       "parameters": {"basePositionPct": 101}})[0], 422)

    def test_live_cost_configuration_matches_analysis_and_rejects_invalid_costs(self) -> None:
        """Propagate the same user cost budget into live observation instead of hardcoding a cheaper floor."""
        query = urlencode({"strategyType": "intraday_scalp", "timeframe": "intraday", "feeRate": .001, "slippageBps": 12})
        status, result = self.request("/api/market?" + query)
        self.assertEqual(status, 200)
        self.assertEqual(result["strategy"]["costAssumption"], {"feeRate": .001, "slippageBps": 12})
        self.assertEqual(self.request("/api/market?" + query + "&feeRate=-1")[0], 422)

    def test_live_excludes_unfinished_minute_from_formal_signal(self) -> None:
        """Keep a forming price sample visible in the chart while evaluating only completed minutes."""
        bars = minute_bars(count=80)
        with patch("app.main.fetch_bars", return_value=(bars, {})), patch(
            "app.main.completed_custom_bars", return_value=(bars.iloc[:-1].copy(), True)
        ):
            status, result = self.request("/api/market?strategyType=intraday_scalp&timeframe=intraday")
        self.assertEqual(status, 200, result)
        self.assertEqual(len(result["bars"]), len(bars))
        self.assertEqual(result["strategy"]["asOf"], bars.iloc[-2]["datetime"].isoformat())
        self.assertEqual(result["strategy"]["barStatus"], "in_progress")

    def test_old_instance_updates_display_without_overwriting_saved_data(self) -> None:
        """Keep strategy identity and custom names while removing obsolete no-overnight copy."""
        original = json.dumps([{"id": "old-intraday", "name": "创业板分钟级日内交易", "strategyType": "intraday_scalp", "description": "旧版不隔夜"}])
        self.store.write_text(original)
        status, items = self.request("/api/strategies")
        self.assertEqual(status, 200)
        self.assertEqual(items[0]["id"], "old-intraday")
        self.assertEqual(items[0]["name"], "创业板底仓做T")
        self.assertIn("basePositionPct", items[0]["parameters"])
        self.assertNotIn("不隔夜", items[0]["description"])
        self.assertEqual(self.store.read_text(), original)


def full_minute_sessions(days: int = 3) -> pd.DataFrame:
    """Provide dated morning/afternoon samples, including the feed's session boundary snapshots."""
    frames = []
    for day in pd.bdate_range("2026-10-26", periods=days):
        dates = pd.date_range(day + pd.Timedelta(570, unit="min"), periods=121, freq="min").append(
            pd.date_range(day + pd.Timedelta(780, unit="min"), periods=121, freq="min"))
        index = np.arange(len(dates))
        close = 10 + len(frames) * .1 + .001 * index + .05 * np.sin(index / 4)
        frames.append(pd.DataFrame({"datetime": dates, "open": close, "high": close + .01,
                                    "low": close - .01, "close": close, "volume": index + 100.}))
    return pd.concat(frames, ignore_index=True)


class DecisionPeriodTest(unittest.TestCase):
    """Ensure selected periods change real decisions without using unfinished bars or bypassing T+1."""

    def test_complete_blocks_align_to_sessions_and_do_not_cross_lunch(self) -> None:
        """A full day yields 48 five-minute or 16 fifteen-minute bars, excluding boundary-only snapshots."""
        raw = full_minute_sessions(1)
        for period, count, first_end in ((5, 48, "09:35"), (15, 16, "09:45")):
            with self.subTest(period=period):
                bars = decision_bars(raw, period)
                self.assertEqual(len(bars), count)
                self.assertEqual(bars.iloc[0]["availableAt"].strftime("%H:%M"), first_end)
                self.assertEqual(bars.iloc[-1]["availableAt"].strftime("%H:%M"), "15:00")
                self.assertFalse(any("11:30" < t.strftime("%H:%M") <= "13:00" for t in bars.availableAt))
                self.assertEqual(bars.iloc[0]["open"], raw.iloc[0]["open"])
                self.assertEqual(bars.iloc[0]["close"], raw.iloc[period - 1]["close"])
                self.assertEqual(bars.iloc[0]["high"], raw.iloc[:period]["high"].max())
                self.assertEqual(bars.iloc[0]["volume"], raw.iloc[:period]["volume"].sum())

    def test_partial_and_missing_minutes_never_form_a_complete_block(self) -> None:
        """A missing minute or a partial tail cannot be replaced with a forward-filled quote."""
        raw = full_minute_sessions(1).iloc[:19]
        self.assertEqual(len(decision_bars(raw, 5)), 3)
        self.assertEqual(len(decision_bars(raw.drop(index=2), 5)), 2)
        self.assertTrue(decision_bars(raw.iloc[:4], 5).empty)

    def test_aggregation_preserves_original_minute_weighted_vwap(self) -> None:
        """Aggregated candle extrema must not silently change the price-volume weighting."""
        raw = full_minute_sessions(2)
        bars = decision_bars(raw, 15)
        indicators = session_indicators(bars, 8, 21, continuous=True)
        for index in (0, 16):
            day = bars.iloc[index]["datetime"].date()
            window = raw[(raw.datetime.dt.date == day) & (raw.datetime <= bars.iloc[index]["datetime"])]
            typical = (window.high + window.low + window.close) / 3
            expected = (typical * window.volume).sum() / window.volume.sum()
            self.assertAlmostEqual(indicators["vwap"][index], expected)

    def test_fifteen_minute_indicators_can_warm_up_from_prior_sessions(self) -> None:
        """EMA21/ATR20 must become available even though each session contains only 16 decision bars."""
        bars = decision_bars(full_minute_sessions(), 15)
        indicators = session_indicators(bars, 8, 21, continuous=True)
        self.assertTrue(np.isnan(indicators["slow"][:20]).all())
        self.assertTrue(np.isfinite(indicators["slow"][20:]).all())
        self.assertTrue(np.isfinite(indicators["atr"][20:]).all())
        live = analyze_strategy(full_minute_sessions(), "intraday_scalp", 8, 21, "159977", "intraday",
                                parameters={"decisionMinutes": 15})
        self.assertNotEqual(live["signal"], "数据不足")
        self.assertEqual(live["asOf"], "2026-10-28T15:00:00")

    def test_new_session_does_not_reuse_yesterdays_last_signal(self) -> None:
        """A new day with fewer than one full bar has no current-session decision timestamp."""
        raw = full_minute_sessions(3).iloc[:242 * 2 + 3]
        live = analyze_strategy(raw, "intraday_scalp", 8, 21, "159977", "intraday", parameters={"decisionMinutes": 15})
        self.assertEqual(live["signal"], "数据不足")
        self.assertIsNone(live["asOf"])

    def test_signals_execute_after_close_and_wait_counts_decision_bars(self) -> None:
        """The same observation fires at the selected close, and waiting spans 3 selected-period bars."""
        raw = full_minute_sessions(1)
        raw[["open", "close", "high", "low"]] = [10.5, 10., 10.52, 9.98]

        def scripted(_close, _indicators, current, *_args):
            """Emit one sell observation and persistent subsequent recovery observations."""
            return current == 2, current >= 3, .1

        for period, sale, buy in ((5, "09:45", "10:00"), (15, "10:15", "11:00")):
            with self.subTest(period=period), patch("app.intraday.base_t_condition", side_effect=scripted):
                result = run_backtest(raw, 100_000, 0, 0, 3, 8, "intraday_scalp",
                    parameters={"decisionMinutes": period, "minHoldBars": 3}, _include_cost_sensitivity=False)
                self.assertEqual([fill["time"][11:16] for fill in result["executions"]], [sale, buy])
                self.assertEqual(result["baseT"]["lockedShares"], result["executions"][1]["shares"])

    def test_no_sale_before_an_incomplete_bar_eventually_finishes(self) -> None:
        """Appending the final minute of a group can never create a trade at a previous timestamp."""
        raw = full_minute_sessions(1)
        raw[["open", "close", "high", "low"]] = [10.5, 10., 10.52, 9.98]

        def scripted(_close, _indicators, current, *_args):
            """Sell on the third complete decision block only."""
            return current == 2, False, .1

        with patch("app.intraday.base_t_condition", side_effect=scripted):
            short = run_backtest(raw.iloc[:14], 100_000, 0, 0, 3, 8, "intraday_scalp",
                parameters={"decisionMinutes": 5}, _include_cost_sensitivity=False)
            full = run_backtest(raw.iloc[:16], 100_000, 0, 0, 3, 8, "intraday_scalp",
                parameters={"decisionMinutes": 5}, _include_cost_sensitivity=False)
        self.assertEqual(short["executions"], [])
        self.assertEqual(full["executions"][0]["time"], "2026-10-26T09:45:00")
        self.assertEqual(short["equityCurve"], full["equityCurve"][:14])

    def test_prefix_equity_and_fills_remain_unchanged_for_all_periods(self) -> None:
        """The future part of a still-forming 5/15-minute bar cannot affect earlier account values."""
        raw = full_minute_sessions()
        for period in (1, 5, 15):
            with self.subTest(period=period):
                args = (100_000, .0003, 5, 8, 21, "intraday_scalp")
                short = run_backtest(raw.iloc[:613], *args, parameters={"decisionMinutes": period}, _include_cost_sensitivity=False)
                full = run_backtest(raw, *args, parameters={"decisionMinutes": period}, _include_cost_sensitivity=False)
                cutoff = raw.iloc[612]["datetime"].isoformat()
                self.assertEqual(short["equityCurve"], full["equityCurve"][:613])
                self.assertEqual(short["executions"], [fill for fill in full["executions"] if fill["time"] <= cutoff])

    def test_empty_aggregated_sample_reports_insufficient_data(self) -> None:
        """Less than one decision bar yields an honest baseline-only result without indexing errors."""
        raw = minute_bars(count=4)
        result = run_backtest(raw, 100_000, 0, 0, 8, 21, "intraday_scalp", parameters={"decisionMinutes": 15})
        self.assertEqual(result["baseT"]["decisionBarCount"], 0)
        self.assertEqual(result["tradeCount"], 0)
        live = analyze_strategy(raw, "intraday_scalp", 8, 21, "159977", "intraday", parameters={"decisionMinutes": 15})
        self.assertEqual(live["signal"], "数据不足")


class DecisionPeriodHTTPTest(LocalStrategyAPITest):
    """Check the editable enum and its persistence/live/backtest contracts over actual HTTP."""

    def test_roundtrip_and_execution_period_for_all_options(self) -> None:
        """Saving a period and using it for analysis/backtest returns the same chosen value."""
        raw = full_minute_sessions()
        today = raw[raw.datetime.dt.date == raw.datetime.iloc[-1].date()].reset_index(drop=True)

        def source(_symbol, period):
            """Return dated history separately from the active chart session."""
            return (raw if period == "five_day" else today), {"marketOpen": False}

        with patch("app.main.fetch_bars", side_effect=source):
            for period in (1, 5, 15):
                with self.subTest(period=period):
                    status, saved = self.request("/api/strategies", {"name": f"做T{period}分", "strategyType": "intraday_scalp", "parameters": {"decisionMinutes": period}})
                    self.assertEqual(status, 200)
                    self.assertEqual(saved["parameters"]["decisionMinutes"], period)
                    query = urlencode({"timeframe": "intraday", "strategyType": "intraday_scalp", "parameters": json.dumps({"decisionMinutes": period})})
                    status, live = self.request("/api/market?" + query)
                    self.assertEqual(status, 200, live)
                    self.assertEqual(live["strategy"]["decisionMinutes"], period)
                    self.assertEqual(len(live["bars"]), len(today))
                    if period > 1:
                        self.assertNotEqual(live["strategy"]["signal"], "数据不足")
                        self.assertIn("最近五日", live["metadata"]["analysisBasis"])
                    status, backtest = self.request("/api/backtest", {"timeframe": "intraday", "strategyType": "intraday_scalp", "parameters": {"decisionMinutes": period}})
                    self.assertEqual(status, 200, backtest)
                    self.assertEqual(backtest["baseT"]["decisionMinutes"], period)
        self.assertEqual({item["parameters"]["decisionMinutes"] for item in self.request("/api/strategies")[1]}, {1, 5, 15})

    def test_unknown_period_is_422_for_save_live_and_backtest(self) -> None:
        """Reject values outside the enum even if numerically between 1 and 15."""
        for period in (0, 2, 10, 16, 5.5, True, "5"):
            with self.subTest(period=period):
                payload = {"name": "bad", "strategyType": "intraday_scalp", "timeframe": "intraday", "parameters": {"decisionMinutes": period}}
                self.assertEqual(self.request("/api/strategies", payload)[0], 422)
                self.assertEqual(self.request("/api/backtest", payload)[0], 422)
                query = urlencode({"strategyType": "intraday_scalp", "parameters": json.dumps(payload["parameters"])})
                self.assertEqual(self.request("/api/market?" + query)[0], 422)

    def test_chart_switch_remains_available_and_explains_selected_basis(self) -> None:
        """Daily browsing remains available while the selected 15-minute strategy is explicitly inactive."""
        query = urlencode({"strategyType": "intraday_scalp", "timeframe": "daily", "parameters": json.dumps({"decisionMinutes": 15})})
        status, result = self.request("/api/market?" + query)
        self.assertEqual(status, 200)
        self.assertFalse(result["strategy"]["supported"])
        self.assertIn("15分钟", result["strategy"]["message"])
        self.assertIn("实时策略不生效", result["strategy"]["message"])


if __name__ == "__main__":
    unittest.main()
