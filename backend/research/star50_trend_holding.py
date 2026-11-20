"""Reproduce the fixed 588080 candidate evaluation without changing production rules.

Run with PYTHONPATH=backend. Input files contain public OHLCV bars (either a list,
or a backtest response with a ``bars`` list). No strategy records are written.
"""

from __future__ import annotations

import argparse
from datetime import timedelta
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from app.star50 import Star50Signals, run_star50_backtest
from app.strategies import normalize_strategy_parameters


RULE_VERSION = "star50_trend_holding_research_v2"


class TrendHoldingSignals(Star50Signals):
    """Share the original execution ledger while replacing only the declared daily signals."""

    def __init__(self, bars: pd.DataFrame, fast: int, slow: int, params: dict) -> None:
        """Use contemporaneous averages and completed closes, with no future-price references."""
        super().__init__(bars, fast, slow, params)
        previous_slow = pd.Series(self.slow).shift(params["slopeBars"]).to_numpy()
        above_fast = self.close > self.fast
        below_slow = self.close <= self.slow
        self.trend = (self.fast > self.slow) & (self.slow > previous_slow)
        self.entry = self.ready & self.trend & above_fast & pd.Series(above_fast).shift(1, fill_value=False).to_numpy()
        self.exit = self.ready & below_slow & pd.Series(below_slow).shift(1, fill_value=False).to_numpy()


def read_snapshot(path: Path) -> pd.DataFrame:
    """Require valid dated OHLCV; reject malformed bars rather than fabricate missing prices."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    bars = pd.DataFrame(payload["bars"] if isinstance(payload, dict) else payload)
    bars = bars.loc[:, ["datetime", "open", "high", "low", "close", "volume"]].copy()
    bars["datetime"] = pd.to_datetime(bars["datetime"], errors="raise").dt.normalize()
    fields = ["open", "high", "low", "close", "volume"]
    bars[fields] = bars[fields].apply(pd.to_numeric, errors="raise")
    if bars.empty or bars["datetime"].isna().any() or bars["datetime"].duplicated().any():
        raise ValueError(f"{path.name}: expected unique valid trading dates")
    if not np.isfinite(bars[fields].to_numpy()).all():
        raise ValueError(f"{path.name}: nonfinite OHLCV")
    if (bars[fields[:4]] <= 0).any().any() or (bars["volume"] < 0).any():
        raise ValueError(f"{path.name}: invalid price or volume")
    if ((bars["high"] < bars[["open", "close", "low"]].max(axis=1))
            | (bars["low"] > bars[["open", "close", "high"]].min(axis=1))).any():
        raise ValueError(f"{path.name}: inconsistent OHLC bounds")
    return bars.sort_values("datetime").reset_index(drop=True)


def account_window(curve: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    """Measure a carried account between dates, including the first day's return and drawdown."""
    values = curve.loc[(curve.index >= start) & (curve.index < end)]
    before = curve.loc[curve.index < start]
    initial = float(before.iloc[-1]) if len(before) else 100_000.0
    path = np.r_[initial, values.to_numpy()]
    return {"start": str(start.date()), "endExclusive": str(end.date()), "bars": len(values),
            "totalReturnPct": float((path[-1] / initial - 1) * 100),
            "maxDrawdownPct": float((path / np.maximum.accumulate(path) - 1).min() * 100)}


def summarize(result: dict, bars: pd.DataFrame, warmup_bars: int = 64) -> dict:
    """Report cost checks, carried monthly rolling windows and arithmetic profit attribution."""
    keys = ("totalReturnPct", "annualizedReturnPct", "maxDrawdownPct", "sharpe",
            "tradeCount", "winRatePct", "exposurePct", "costSensitivity")
    summary = {key: result[key] for key in keys}
    curve = pd.Series([item["value"] for item in result["equityCurve"]], index=bars["datetime"])
    summary["calendarWindows"] = [account_window(curve, pd.Timestamp(year, 1, 1), pd.Timestamp(year + 1, 1, 1))
                                  for year in range(2021, int(curve.index[-1].year) + 1)]
    # Keep signal/account state from the full causal simulation. Overlapping windows are not independent.
    rolling = []
    first_month = (curve.index[warmup_bars] + pd.offsets.MonthBegin(1)).normalize()
    for start in pd.date_range(first_month, curve.index[-1], freq="MS"):
        end = start + pd.DateOffset(years=1)
        if end.date() <= curve.index[-1].date() + timedelta(days=1):
            rolling.append(account_window(curve, start, end))
    summary["rolling12Months"] = rolling
    window_returns = np.asarray([row["totalReturnPct"] for row in rolling])
    summary["rollingSummary"] = {
        "windowCount": len(rolling),
        "positiveWindowPct": float(np.mean(window_returns > 0) * 100) if len(rolling) else None,
        "medianReturnPct": float(np.median(window_returns)) if len(rolling) else None,
        "worstReturnPct": float(window_returns.min()) if len(rolling) else None,
    }
    winners = sorted((row["pnl"] for row in result["trades"] if row["pnl"] > 0), reverse=True)
    largest = winners[0] if winners else 0.0
    pnl = result["finalEquity"] - 100_000
    summary["profitAttribution"] = {
        "totalPnl": pnl, "largestWinnerPnl": largest,
        "largestWinnerPctOfGrossProfit": largest / sum(winners) * 100 if winners else None,
        # This is arithmetic attribution, not a resimulation with a trade removed or cash reinvested.
        "netPnlLessLargestWinner": pnl - largest,
        "openPositions": result["openPositions"],
    }
    summary["totalCost"] = result["riskManagement"]["totalTransactionCost"]
    return summary


def evaluate(primary: Path, reference: Path) -> dict:
    """Use common trading dates, identical costs and equal-risk controls for the fixed hypothesis."""
    sources = [("primary", read_snapshot(primary)), ("reference", read_snapshot(reference))]
    common = pd.Index(sources[0][1]["datetime"]).intersection(pd.Index(sources[1][1]["datetime"]))
    if len(common) < 316:
        raise ValueError("Need warmup and at least one year on common trading dates")
    baseline = normalize_strategy_parameters("star50_breakout")
    output = {"ruleVersion": RULE_VERSION, "productionUpdated": False,
              "candidateRules": {"fastPeriod": 20, "slowPeriod": 60, "slopeBars": 5,
                                 "confirmationBars": 2, "riskBudgetPct": 2, "maxPositionPct": 80,
                                 "initialAtrMultiplier": 2.5, "trailingAtrMultiplier": 3,
                                 "gapAtrMultiplier": 1, "meanExitPeriod": 60},
              "assumptions": {"initialCapital": 100000, "feeRate": .0003, "slippageBps": 5,
                              "history": "adjusted-price research approximation",
                              "validation": "retrospective; full history was already observed",
                              "rollingWindows": "12 months, monthly step, carried account, overlapping"},
              "sources": [], "results": []}
    scenarios = [("original", Star50Signals, .5, 50), ("original_risk2", Star50Signals, 2, 80),
                 ("candidate_same_risk", TrendHoldingSignals, .5, 50),
                 ("candidate_approved", TrendHoldingSignals, 2, 80)]
    for (source, original), snapshot in zip(sources, (primary, reference)):
        bars = original.loc[original["datetime"].isin(common)].reset_index(drop=True)
        excluded = original.loc[~original["datetime"].isin(common), "datetime"].dt.strftime("%Y-%m-%d").tolist()
        output["sources"].append({"source": source, "snapshotName": snapshot.name,
                                  "snapshotSha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                                  "inputBars": len(original), "commonBars": len(bars),
                                  "start": str(bars.datetime.iloc[0].date()),
                                  "end": str(bars.datetime.iloc[-1].date()), "excludedDates": excluded})
        for name, signals, risk, cap in scenarios:
            params = {**baseline, "riskBudgetPct": risk, "maxPositionPct": cap}
            # The override is scoped to this offline process and includes sensitivity reruns only.
            with patch("app.star50.Star50Signals", signals):
                result = run_star50_backtest(bars, 100000, .0003, 5, 20, 60, params)
            output["results"].append({"source": source, "scenario": name, **summarize(result, bars)})
    # The approved plan disallows replacing defaults when a few trades dominate profitability.
    candidates = [row for row in output["results"] if row["scenario"] == "candidate_approved"]
    reasons = []
    if any(row["profitAttribution"]["netPnlLessLargestWinner"] <= 0 for row in candidates):
        reasons.append("Net profit depends on the largest winning trade in at least one price source")
    if any(row["maxDrawdownPct"] < -20 for row in candidates):
        reasons.append("Historical drawdown exceeds the declared 20 percent research target")
    output["decision"] = {"replaceDefault": False,
                          "status": "rejected" if reasons else "requires_further_validation",
                          "reasons": reasons or ["Historical improvements alone do not establish a stable edge"]}
    return output


def main() -> None:
    """Write research JSON only; never migrate or save application strategies."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--primary", required=True, type=Path)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    resolved_output = args.output.resolve()
    if resolved_output in {args.primary.resolve(), args.reference.resolve()}:
        parser.error("Output must not overwrite an input snapshot")
    result = evaluate(args.primary, args.reference)
    resolved_output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    for row in result["results"]:
        print(json.dumps({key: value for key, value in row.items()
                          if key not in {"calendarWindows", "rolling12Months", "costSensitivity"}}, ensure_ascii=False))
    print(json.dumps(result["decision"], ensure_ascii=False))


if __name__ == "__main__":
    main()
