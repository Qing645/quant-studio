"""Reproduce the fixed price-channel alternative; report all inspected risks, never rank a grid."""

import argparse
import hashlib
import json
from pathlib import Path

from app.engine import run_backtest
from app.star50 import _summary
from research.star50_trend_holding import read_snapshot
import numpy as np


def evaluate(path: Path) -> dict:
    """Use native dates, 10,000 capital, real lots/costs and disclosed neighboring-rule diagnostics."""
    bars = read_snapshot(path)
    rows = []
    for name, fast, slow, parameters in (
        ("replaced_pullback", 5, 200, None),
        ("channel_slope20", 10, 20, {"trendSlopeBars": 20}),
        ("channel_slope50", 10, 20, {"trendSlopeBars": 50}),
        ("channel_slope80", 10, 20, {"trendSlopeBars": 80}),
        ("channel_slope100", 10, 20, {"trendSlopeBars": 100}),
        ("channel_volume1", 10, 20, {"volumeMultiplier": 1}),
        ("channel_volume12", 10, 20, {"volumeMultiplier": 1.2}),
        ("channel_volume15", 10, 20, {"volumeMultiplier": 1.5}),
        ("channel_trail15", 10, 20, {"trailingAtrMultiplier": 1.5}),
        ("channel_trail2", 10, 20, {"trailingAtrMultiplier": 2}),
        ("channel_trail25", 10, 20, {"trailingAtrMultiplier": 2.5}),
        ("channel_trail3", 10, 20, {"trailingAtrMultiplier": 3}),
        ("channel_v6_trail6", 10, 20, {"trailingAtrMultiplier": 6}),
        ("channel_entry12_trail2", 10, 12, {"trailingAtrMultiplier": 2}),
        ("channel_entry14_trail2", 10, 14, {"trailingAtrMultiplier": 2}),
        ("channel_entry15_trail2", 10, 15, {"trailingAtrMultiplier": 2}),
        ("channel_entry16_trail2", 10, 16, {"trailingAtrMultiplier": 2}),
        ("channel_entry18_trail2", 10, 18, {"trailingAtrMultiplier": 2}),
        ("channel_entry20_trail2", 10, 20, {"trailingAtrMultiplier": 2}),
        ("channel_slope20_entry15_trail2", 10, 15, {"trendSlopeBars": 20, "trailingAtrMultiplier": 2}),
        ("channel_slope30_entry15_trail2", 10, 15, {"trendSlopeBars": 30, "trailingAtrMultiplier": 2}),
        ("channel_slope50_entry15_trail2", 10, 15, {"trendSlopeBars": 50, "trailingAtrMultiplier": 2}),
        ("channel_exit5", 5, 20, {}),
        ("channel_exit7", 7, 20, {}),
        ("channel_exit15", 15, 20, {}),
        ("channel_initial1", 10, 20, {"initialAtrMultiplier": 1}),
        ("channel_risk3", 10, 20, {"riskBudgetPct": 3}),
    ):
        kind = "star50_pullback" if parameters is None else "star50_channel"
        params = parameters
        if kind == "star50_channel":
            params = {"riskBudgetPct": 4, "maxPositionPct": 90, "initialAtrMultiplier": 1.5,
                      "trailingAtrMultiplier": 6, "gapAtrMultiplier": 2, **(params or {})}
        result = run_backtest(bars, 10000, .0003, 5, fast, slow, kind, parameters=params)
        winner = max((trade["pnl"] for trade in result["trades"]), default=0)
        years = []
        curve = np.asarray([point["value"] for point in result["equityCurve"]])
        for year in sorted(set(bars.datetime.dt.year)):
            indexes = np.flatnonzero(bars.datetime.dt.year.to_numpy() == year)
            first, last = int(indexes[0]), int(indexes[-1])
            baseline = curve[first - 1] if first else 10000
            years.append({"year": int(year), **_summary(curve[first:last + 1], baseline)})
        rows.append({"name": name, "strategyType": kind, "fastPeriod": fast, "slowPeriod": slow,
                     "parameters": result["parameters"],
                     **{key: result[key] for key in ("totalReturnPct", "annualizedReturnPct", "maxDrawdownPct",
                                                    "exposurePct", "tradeCount", "researchTarget", "costSensitivity")},
                     "calendarYears": years, "largestClosedWinnerPnl": winner,
                     "netPnlLessLargestWinner": result["finalEquity"] - 10000 - winner})
    return {"source": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bars": len(bars), "start": str(bars.datetime.iloc[0].date()), "end": str(bars.datetime.iloc[-1].date()),
            "initialCapital": 10000, "feeRate": .0003, "slippageBps": 5,
            "validation": "Retrospective exploratory comparison; these histories have already been observed. Native snapshots overlap and are not independent samples. Sensitivity results are disclosed, not used to select defaults.",
            "priorMechanisms": "A trend/oversold combination lost money and exceeded 39% drawdown; a 5% risk channel exceeded 21% drawdown. The 4% risk channel reduces sizing; the annual 15% objective remains unmet.",
            "results": rows}


def main() -> None:
    """Write a reproducible report without changing any strategy configuration or source snapshot."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error("Output must not overwrite the source")
    report = evaluate(args.input)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    for row in report["results"]:
        print(json.dumps({key: row[key] for key in ("name", "totalReturnPct", "annualizedReturnPct", "maxDrawdownPct", "netPnlLessLargestWinner")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
