"""Reproduce the fixed price-channel alternative; report all inspected risks, never rank a grid."""

import argparse
import hashlib
import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from app.engine import run_backtest
from app.star50 import Star50ChannelSignals, _summary
from app.strategies import normalize_strategy_parameters
from research.star50_trend_holding import read_snapshot
import numpy as np
import pandas as pd


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


def compare_saved(path: Path, strategy: dict) -> dict:
    """Compare five declared single-factor candidates with shared warmup, costs and unchanged storage."""
    if strategy["strategyType"] != "star50_channel":
        raise ValueError("仅支持科创50通道策略")
    bars = read_snapshot(path)
    params = normalize_strategy_parameters("star50_channel", strategy["parameters"])
    fast, slow = strategy["fastPeriod"], strategy["slowPeriod"]
    variants = (
        ("当前保存参数", {}),
        ("跟踪2.5ATR", {"trailingAtrMultiplier": 2.5}),
        ("跟踪3ATR", {"trailingAtrMultiplier": 3}),
        ("趋势SMA150", {"trendFilterPeriod": 150}),
        ("趋势SMA100", {"trendFilterPeriod": 100}),
    )
    configurations = [(name, normalize_strategy_parameters("star50_channel", {**params, **override}))
                      for name, override in variants]
    first_ready = max(max(p["trendFilterPeriod"] + p["trendSlopeBars"] - 1, slow, fast, 20)
                      for _, p in configurations)
    if len(bars) <= first_ready + 252:
        raise ValueError("共同预热后至少需要252根日K才能比较稳定性")

    class CommonWarmupSignals(Star50ChannelSignals):
        """Keep all candidates in cash until the same history-only eligibility index."""

        def __init__(self, data, fast_period, slow_period, settings):
            """Reuse original signals while aligning benchmark and entry eligibility."""
            super().__init__(data, fast_period, slow_period, settings)
            self.ready[:first_ready] = False
            self.entry[:first_ready] = False

    initial = 10000
    results = []
    for name, settings in configurations:
        with patch("app.star50.Star50ChannelSignals", CommonWarmupSignals):
            result = run_backtest(bars, initial, .0003, 5, fast, slow,
                                  "star50_channel", parameters=settings)
        curve = np.asarray([point["value"] for point in result["equityCurve"]])

        def window_stats(indexes):
            """Measure the carried account including the return into the first bar of each window."""
            first, last = int(indexes[0]), int(indexes[-1])
            base = curve[first - 1] if first else initial
            return {"start": str(bars.datetime.iloc[first].date()),
                    "end": str(bars.datetime.iloc[last].date()),
                    **_summary(curve[first:last + 1], base)}

        years = [{"year": int(year), **window_stats(np.flatnonzero(bars.datetime.dt.year == year))}
                 for year in sorted(set(bars.datetime.dt.year))]
        rolling = []
        first_month = bars.datetime.iloc[first_ready] + pd.offsets.MonthBegin(1)
        for start in pd.date_range(first_month, bars.datetime.iloc[-1], freq="MS"):
            end = start + pd.DateOffset(years=1)
            if end.date() > bars.datetime.iloc[-1].date() + timedelta(days=1):
                continue
            indexes = np.flatnonzero((bars.datetime >= start) & (bars.datetime < end))
            if indexes.size:
                rolling.append(window_stats(indexes))
        net_pnl = result["finalEquity"] - initial
        largest = max((trade["pnl"] for trade in result["trades"] if trade["pnl"] > 0), default=0)
        results.append({
            "name": name, "parameters": settings,
            **{key: result[key] for key in ("totalReturnPct", "annualizedReturnPct", "maxDrawdownPct",
                                           "tradeCount", "exposurePct", "costSensitivity")},
            "calendarYears": years, "rolling12Months": rolling,
            "worstRolling12MonthReturnPct": min((x["totalReturnPct"] for x in rolling), default=None),
            "netPnl": net_pnl, "largestWinnerPnl": largest,
            "largestWinnerPctOfNetPnl": largest / net_pnl * 100 if net_pnl > 0 else None,
            "netPnlLessLargestWinner": net_pnl - largest,
            "exitCounts": {reason: sum(t["exitReason"] == reason for t in result["trades"])
                           for reason in sorted({t["exitReason"] for t in result["trades"]})},
        })
    return {
        "source": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "start": str(bars.datetime.iloc[0].date()), "end": str(bars.datetime.iloc[-1].date()),
        "bars": len(bars), "firstEligibleExecutionDate": str(bars.datetime.iloc[first_ready + 1].date()),
        "savedStrategyId": strategy["id"], "savedVersion": strategy["version"],
        "initialCapital": initial, "feeRate": .0003, "slippageBps": 5,
        "validation": "固定五组、单因素探索比较；历史样本已被观察，不是独立样本外验证。年度及滚动窗口沿用连续账户，滚动窗口彼此重叠。年化含共同预热期现金等待。",
        "attribution": "净利润减最大盈利是金额归因，不是移除该交易并重新复利回测。",
        "results": results,
    }


def main() -> None:
    """Write a reproducible report without changing any strategy configuration or source snapshot."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--saved-strategies", type=Path, help="按当前保存通道参数比较固定五组")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; choose a new report path")
    if args.input.resolve() == args.output.resolve() or (
        args.saved_strategies and args.saved_strategies.resolve() == args.output.resolve()
    ):
        parser.error("Output must not overwrite the source")
    if args.saved_strategies:
        saved = json.loads(args.saved_strategies.read_text(encoding="utf-8"))
        strategy = next(item for item in saved if item["strategyType"] == "star50_channel")
        report = compare_saved(args.input, strategy)
    else:
        report = evaluate(args.input)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    for row in report["results"]:
        print(json.dumps({key: row[key] for key in ("name", "totalReturnPct", "annualizedReturnPct", "maxDrawdownPct", "netPnlLessLargestWinner")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
