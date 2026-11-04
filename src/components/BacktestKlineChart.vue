<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from "vue";
import * as echarts from "echarts/core";
import { CandlestickChart, LineChart } from "echarts/charts";
import { DataZoomComponent, GridComponent, LegendComponent, MarkPointComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { BacktestResult, Bar } from "../types";

type Trade = BacktestResult["trades"][number];
type OpenPosition = NonNullable<BacktestResult["openPositions"]>[number];

const props = defineProps<{
  symbol: string;
  bars: Bar[];
  trades: Trade[];
  openPositions: OpenPosition[];
}>();
const element = ref<HTMLDivElement>();
let chart: echarts.ECharts | undefined;

echarts.use([CandlestickChart, LineChart, DataZoomComponent, GridComponent, LegendComponent, MarkPointComponent, TooltipComponent, CanvasRenderer]);

/** Draw the exact backtest bars with fills anchored to the executed bar and price. */
function drawChart(): void {
  if (!element.value || !props.bars.length) return;
  chart?.dispose();
  chart = echarts.init(element.value);
  const bars = props.bars;
  const timeKey = (value: string) => value.replace("T", " ").slice(0, 19);
  const indexByTime = new Map(bars.map((bar, index) => [timeKey(bar.datetime), index]));
  const indexByDate = new Map(bars.map((bar, index) => [bar.datetime.slice(0, 10), index]));

  /** Normalize ISO and API timestamps before exact matching, then fall back to the same date. */
  const markerFor = (action: "买入" | "卖出", time: string, price: number) => {
    const index = indexByTime.get(timeKey(time)) ?? indexByDate.get(time.slice(0, 10));
    if (index === undefined) return [];
    return [{
      name: `${props.symbol} · ${action} · ${time.slice(0, 16)}`,
      coord: [index, price] as [number, number],
      value: +price.toFixed(3),
      symbol: "triangle",
      symbolRotate: action === "卖出" ? 180 : 0,
      itemStyle: { color: action === "买入" ? "#ed786a" : "#40c99b", borderColor: "#101817", borderWidth: 1 },
    }];
  };
  const markers = [
    ...props.trades.flatMap((trade) => [
      ...markerFor("买入", trade.entry, trade.entryPrice),
      ...markerFor("卖出", trade.exit, trade.exitPrice),
    ]),
    ...props.openPositions.flatMap((position) => markerFor("买入", position.entry, position.entryPrice)),
  ];
  const movingAverage = (period: number) => bars.map((_, index) => {
    if (index < period - 1) return "-";
    return +(bars.slice(index - period + 1, index + 1).reduce((sum, bar) => sum + bar.close, 0) / period).toFixed(4);
  });
  const labels = bars.map((bar) => bar.datetime.length > 10 && bar.datetime.slice(11, 16) !== "00:00"
    ? bar.datetime.slice(5, 16)
    : bar.datetime.slice(0, 10));

  chart.setOption({
    animation: false,
    grid: { left: 55, right: 20, top: 28, bottom: 44 },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross" },
      backgroundColor: "#182321",
      borderColor: "#2a3b37",
      textStyle: { color: "#e4efeb" },
    },
    legend: { top: 0, right: 8, textStyle: { color: "#85948e", fontSize: 11 }, data: ["K线", "SMA20", "SMA60"] },
    xAxis: { type: "category", data: labels, boundaryGap: true, axisLine: { lineStyle: { color: "#293733" } }, axisLabel: { color: "#788983", hideOverlap: true, fontSize: 10 } },
    yAxis: { type: "value", scale: true, splitNumber: 5, axisLabel: { color: "#788983", fontSize: 10 }, splitLine: { lineStyle: { color: "#1e2a27" } } },
    dataZoom: [
      { type: "inside", start: 0, end: 100 },
      { type: "slider", height: 14, bottom: 1, borderColor: "#24322e", fillerColor: "rgba(39, 191, 144, .11)", textStyle: { color: "#687a73" } },
    ],
    series: [
      {
        name: "K线",
        type: "candlestick",
        data: bars.map((bar) => [bar.open, bar.close, bar.low, bar.high]),
        itemStyle: { color: "#e36d62", color0: "#27b78b", borderColor: "#e36d62", borderColor0: "#27b78b" },
        markPoint: {
          symbolSize: 14,
          label: { show: false },
          tooltip: { formatter: "{b}<br/>执行价：{c}" },
          data: markers,
        },
      },
      { name: "SMA20", type: "line", data: movingAverage(20), showSymbol: false, lineStyle: { width: 1.2, color: "#efba63" } },
      { name: "SMA60", type: "line", data: movingAverage(60), showSymbol: false, lineStyle: { width: 1.2, color: "#67aee8" } },
    ],
  });
}

/** Resize the chart with its panel and release its canvas on unmount. */
function resizeChart(): void { chart?.resize(); }

watch(() => [props.bars, props.trades, props.openPositions], drawChart, { deep: true });
onMounted(() => {
  drawChart();
  window.addEventListener("resize", resizeChart);
});
onUnmounted(() => {
  window.removeEventListener("resize", resizeChart);
  chart?.dispose();
});
</script>

<template><div ref="element" class="backtest-kline-chart"></div></template>
