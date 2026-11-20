"""Compare a small, declared set of 588080 hypotheses without optimizing a parameter grid."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import talib

from app.star50 import Star50Signals, _summary, run_star50_backtest
from app.strategies import normalize_strategy_parameters
from research.star50_trend_holding import TrendHoldingSignals, read_snapshot, summarize


COMMON_WARMUP = 200
RESEARCH_VERSION = "star50_fixed_hypotheses_v3"
DEFAULT_ANNUAL_TARGET = 15.0
MAX_DRAWDOWN_TARGET = 20.0


class SlowTrendSignals(TrendHoldingSignals):
    """Keep v2 entries and the slow-average exit; investigate a wider ATR trail."""


class TrendPullbackSignals(Star50Signals):
    """Study short oversold recoveries only while price remains above the long trend."""

    trend_period = 200
    rsi_threshold = 10
    recovery_period = 5

    def __init__(self, bars: pd.DataFrame, fast: int, slow: int, params: dict) -> None:
        """Use the conventional RSI2<=10/SMA200 filter and SMA5 recovery exit."""
        super().__init__(bars, fast, slow, params)
        long_mean = talib.SMA(self.close, timeperiod=self.trend_period)
        recovery = talib.SMA(self.close, timeperiod=self.recovery_period)
        rsi = talib.RSI(self.close, timeperiod=2)
        self.ready &= np.isfinite(long_mean) & np.isfinite(recovery) & np.isfinite(rsi)
        self.entry = self.ready & (self.close > long_mean) & (rsi <= self.rsi_threshold)
        self.exit = self.ready & (self.close >= recovery)


class BandReversionSignals(Star50Signals):
    """Study oversold band reversions with the unchanged account risk limits."""

    def __init__(self, bars: pd.DataFrame, fast: int, slow: int, params: dict) -> None:
        """Enter below the 20-day two-deviation lower band and exit at the middle band."""
        super().__init__(bars, fast, slow, params)
        _, middle, lower = talib.BBANDS(self.close, timeperiod=20, nbdevup=2, nbdevdn=2)
        self.ready &= np.isfinite(middle) & np.isfinite(lower)
        self.entry = self.ready & (self.close < lower)
        self.exit = self.ready & (self.close >= middle)


class FastRecoverySignals(Star50Signals):
    """Study early trend recovery without waiting for a 60-day rising-average gate."""

    def __init__(self, bars: pd.DataFrame, fast: int, slow: int, params: dict) -> None:
        """Require two closes above a rising SMA20; exit on two closes at/below SMA20."""
        super().__init__(bars, fast, slow, params)
        above = self.close > self.fast
        below = self.close <= self.fast
        rising = self.fast > pd.Series(self.fast).shift(5).to_numpy()
        self.entry = self.ready & rising & above & pd.Series(above).shift(1, fill_value=False).to_numpy()
        self.exit = self.ready & below & pd.Series(below).shift(1, fill_value=False).to_numpy()


@dataclass(frozen=True)
class Hypothesis:
    """Declare rules and risk controls before running any of the fixed comparisons."""

    name: str
    signals: type[Star50Signals]
    description: str
    trail: float = 3


HYPOTHESES = (
    Hypothesis("original_risk2", Star50Signals, "原突破规则；风险2%、上限80%、跟踪3ATR"),
    Hypothesis("trend_holding_v2", TrendHoldingSignals, "前轮候选；20/60均线趋势恢复，退出SMA60/3ATR"),
    Hypothesis("slow_trend_trail5", SlowTrendSignals, "保留前轮入场与SMA60退出，仅将跟踪距离由3ATR放宽为5ATR", 5),
    Hypothesis("trend_rsi2_pullback", TrendPullbackSignals, "收盘>SMA200且RSI2≤10入场；收盘≥SMA5退出"),
    Hypothesis("band20_reversion", BandReversionSignals, "收盘低于20日布林带下轨2标准差入场；回到中轨退出"),
    Hypothesis("fast20_recovery", FastRecoverySignals, "两根收盘>SMA20且SMA20高于5日前入场；两根≤SMA20退出"),
)


def simulate(bars: pd.DataFrame, hypothesis: Hypothesis, *, fee: float = .0003, slippage: float = 5,
             warmup: int = COMMON_WARMUP, initial: float = 100000, risk: float = 2) -> dict:
    """Apply identical funding and execution rules and a common history-only warmup."""
    params = {**normalize_strategy_parameters("star50_breakout"), "riskBudgetPct": risk,
              "maxPositionPct": 80, "trailingAtrMultiplier": hypothesis.trail}

    class WarmedSignals(hypothesis.signals):
        """Block entries before the same eligibility date for every hypothesis."""

        def __init__(self, data: pd.DataFrame, fast: int, slow: int, settings: dict) -> None:
            """Keep prehistory available to indicators while preserving initial cash during warmup."""
            super().__init__(data, fast, slow, settings)
            self.entry[:warmup] = False

    with patch("app.star50.Star50Signals", WarmedSignals):
        result = run_star50_backtest(bars, initial, fee, slippage, 20, 60, params)
    return result


def equal_sleeves(bars: pd.DataFrame) -> dict:
    """Allocate 50,000 to each fixed model, with separate cash, round lots and no rebalancing."""
    legs = [simulate(bars, Hypothesis("trend", FastRecoverySignals, "SMA20恢复"), initial=50000),
            simulate(bars, Hypothesis("pullback", TrendPullbackSignals, "RSI2顺势回撤"), initial=50000)]
    values = np.sum([[point["value"] for point in leg["equityCurve"]] for leg in legs], axis=0)
    trades = [{**trade, "sleeve": name} for name, leg in zip(("trend", "pullback"), legs)
              for trade in leg["trades"]]
    sensitivity = []
    for index, label in enumerate(("零成本", "双倍成本")):
        # Each constituent scenario was fully rerun by the shared ledger, including its own lots.
        sensitivity.append({"scenario": label, "totalReturnPct": float(np.mean([
            leg["costSensitivity"][index]["totalReturnPct"] for leg in legs]))})
    fills = {}
    for leg in legs:
        for execution in leg["executions"]:
            fills[execution["time"]] = fills.get(execution["time"], 0) + (
                execution["shares"] if execution["side"] == "buy" else -execution["shares"])
    shares, exposures = 0, []
    for row, value in zip(bars.itertuples(), values):
        shares += fills.get(row.datetime.isoformat(), 0)
        exposures.append(shares * row.close / value * 100)
    result = {**_summary(values, 100000), "initialCapital": 100000, "finalEquity": float(values[-1]),
              "tradeCount": len(trades), "trades": trades,
              "winRatePct": float(np.mean([row["pnl"] > 0 for row in trades]) * 100) if trades else 0,
              "exposurePct": float(np.mean(exposures)),
              "equityCurve": [{"time": row.datetime.isoformat(), "value": float(value)}
                              for row, value in zip(bars.itertuples(), values)],
              "openPositions": [position for leg in legs for position in leg["openPositions"]],
              "riskManagement": {"totalTransactionCost": sum(leg["riskManagement"]["totalTransactionCost"] for leg in legs)},
              "costSensitivity": sensitivity}
    return result


def absolute_checks(row: dict, annual_target: float) -> dict:
    """Evaluate declared absolute criteria without choosing the highest historical return."""
    return {"positiveReturn": row["totalReturnPct"] > 0,
            "annualTargetReached": row["annualizedReturnPct"] >= annual_target,
            "drawdownWithinResearchTarget": row["maxDrawdownPct"] >= -MAX_DRAWDOWN_TARGET,
            "positiveWithDoubleCosts": row["costSensitivity"][1]["totalReturnPct"] > 0,
            "netPositiveLessLargestWinner": row["profitAttribution"]["netPnlLessLargestWinner"] > 0}


def stability_probes(bars: pd.DataFrame, native: pd.DataFrame) -> list[dict]:
    """Disclose neighboring rules, native-date results and risk scaling without selecting new defaults."""
    settings = [("center", {}, 2), ("rsi5", {"rsi_threshold": 5}, 2),
                ("rsi15", {"rsi_threshold": 15}, 2), ("trend180", {"trend_period": 180}, 2),
                ("trend220", {"trend_period": 220}, 2), ("exit4", {"recovery_period": 4}, 2),
                ("exit6", {"recovery_period": 6}, 2), ("risk1", {}, 1), ("risk3", {}, 3)]
    rows = []
    for name, attrs, risk in settings:
        signals = type("FixedPullbackProbe", (TrendPullbackSignals,), attrs)
        warmup = max(COMMON_WARMUP, attrs.get("trend_period", COMMON_WARMUP))
        result = simulate(bars, Hypothesis(name, signals, "fixed sensitivity probe"), warmup=warmup, risk=risk)
        summary = summarize(result, bars, warmup)
        rows.append({"probe": name, "riskBudgetPct": risk, "settings": attrs,
                     **{key: value for key, value in summary.items() if key not in {"rolling12Months", "calendarWindows"}}})
    own_dates = native.loc[native.datetime.between(bars.datetime.iloc[0], bars.datetime.iloc[-1])].reset_index(drop=True)
    result = simulate(own_dates, Hypothesis("native_dates", TrendPullbackSignals, "same rule on native calendar"))
    summary = summarize(result, own_dates, COMMON_WARMUP)
    rows.append({"probe": "native_dates", "bars": len(own_dates), "riskBudgetPct": 2,
                 **{key: value for key, value in summary.items() if key not in {"rolling12Months", "calendarWindows"}}})
    return rows


def evaluate(primary: Path, reference: Path, annual_target: float = DEFAULT_ANNUAL_TARGET) -> dict:
    """Disclose all hypotheses and source differences; do not return a history-ranked winner."""
    sources = [("primary", read_snapshot(primary)), ("reference", read_snapshot(reference))]
    common = pd.Index(sources[0][1].datetime).intersection(pd.Index(sources[1][1].datetime))
    if len(common) < COMMON_WARMUP + 252:
        raise ValueError("Need 200 warmup bars plus one trading year on common dates")
    report = {"version": RESEARCH_VERSION, "productionUpdated": False, "annualTargetPct": annual_target,
              "maxDrawdownTargetPct": MAX_DRAWDOWN_TARGET,
              "hypotheses": [{"name": item.name, "description": item.description,
                               "trailingAtrMultiplier": item.trail} for item in HYPOTHESES],
              "assumptions": {"riskBudgetPct": 2, "maxPositionPct": 80, "initialCapital": 100000,
                              "feeRate": .0003, "slippageBps": 5, "commonWarmup": COMMON_WARMUP,
                              "initialAtrMultiplier": 2.5, "gapAtrMultiplier": 1,
                              "validation": "exploratory; all historical years have already been observed",
                              "profitStress": "arithmetic attribution, not removing and rerunning a trade",
                              "rollingWindows": "overlapping, carried account; not independent samples"},
              "sources": [], "results": [], "stabilityProbes": []}
    report["hypotheses"].append({"name": "equal_sleeves", "description": "SMA20恢复与RSI2回撤各5万元；各自独立现金和整数手，无自动再平衡"})
    for (source, original), path in zip(sources, (primary, reference)):
        bars = original.loc[original.datetime.isin(common)].reset_index(drop=True)
        report["sources"].append({"name": source, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                  "inputBars": len(original), "matchedBars": len(bars),
                                  "start": str(bars.datetime.iloc[0].date()), "end": str(bars.datetime.iloc[-1].date()),
                                  "firstSignalDate": str(bars.datetime.iloc[COMMON_WARMUP].date()),
                                  "nativeDatesNotMatched": original.loc[~original.datetime.isin(common), "datetime"].dt.strftime("%Y-%m-%d").tolist()})
        for hypothesis in HYPOTHESES:
            result = simulate(bars, hypothesis)
            row = {"source": source, "hypothesis": hypothesis.name, **summarize(result, bars, COMMON_WARMUP)}
            row["checks"] = absolute_checks(row, annual_target)
            row["closedTrades"] = result["trades"]
            report["results"].append(row)
        mixed = equal_sleeves(bars)
        mixed_row = {"source": source, "hypothesis": "equal_sleeves", **summarize(mixed, bars, COMMON_WARMUP)}
        mixed_row["checks"] = absolute_checks(mixed_row, annual_target)
        mixed_row["closedTrades"] = mixed["trades"]
        report["results"].append(mixed_row)
        report["stabilityProbes"].extend({"source": source, **probe} for probe in stability_probes(bars, original))
    candidates = [item["name"] for item in report["hypotheses"]
                  if all(all(row["checks"].values()) for row in report["results"]
                         if row["hypothesis"] == item["name"])]
    report["decision"] = {"replaceDefault": False, "candidatesMeetingAbsoluteChecks": candidates,
                          "status": "no_candidate_meets_checks" if not candidates else "requires_further_validation",
                          "reason": "No default replacement from historical ranking alone; examine declared target, concentration and sensitivity"}
    return report


def main() -> None:
    """Write only a research artifact; protect the input snapshots from accidental overwrite."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--primary", required=True, type=Path)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--annual-target-pct", type=float, default=DEFAULT_ANNUAL_TARGET,
                        help="User-selected screening target; default 15 percent, not a promised return")
    args = parser.parse_args()
    if args.output.resolve() in {args.primary.resolve(), args.reference.resolve()}:
        parser.error("Output must not overwrite an input snapshot")
    if not np.isfinite(args.annual_target_pct) or args.annual_target_pct <= 0:
        parser.error("Annual target must be finite and positive")
    result = evaluate(args.primary, args.reference, args.annual_target_pct)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    for row in result["results"]:
        print(json.dumps({key: row[key] for key in ("source", "hypothesis", "totalReturnPct", "annualizedReturnPct",
                                                  "maxDrawdownPct", "tradeCount", "rollingSummary", "profitAttribution", "checks")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
