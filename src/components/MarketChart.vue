<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from "vue";
import * as echarts from "echarts/core";
import { CandlestickChart, LineChart } from "echarts/charts";
import { DataZoomComponent, GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { Bar, Timeframe } from "../types";

const props = defineProps<{ bars: Bar[]; timeframe: Timeframe }>();
const element = ref<HTMLDivElement>();
let chart: echarts.ECharts | undefined;

echarts.use([CandlestickChart, LineChart, DataZoomComponent, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

/** Rebuild the chart when the selected market bars or interval changes. */
function drawChart(): void {
  if (!element.value || !props.bars.length) return;
  chart?.dispose();
  chart = echarts.init(element.value);
  const bars = props.bars;
  const trendChart = props.timeframe === "intraday" || props.timeframe === "five_day";
  const showMovingAverages = props.timeframe !== "intraday";
  const dates = bars.map((bar) => trendChart ? bar.datetime.slice(5, 16) : bar.datetime.slice(0, 10));
  const sma = (period: number) => bars.map((_, index) => {
    if (index < period - 1) return "-";
    return +(bars.slice(index - period + 1, index + 1).reduce((sum, bar) => sum + bar.close, 0) / period).toFixed(4);
  });
  const series = trendChart
    ? [
      { name: "价格", type: "line" as const, data: bars.map((bar) => bar.close), showSymbol: false, lineStyle: { width: 1.7, color: "#49cba0" } },
      ...(showMovingAverages ? [
        { name: "SMA20", type: "line" as const, data: sma(20), showSymbol: false, lineStyle: { width: 1.2, color: "#efba63" } },
        { name: "SMA60", type: "line" as const, data: sma(60), showSymbol: false, lineStyle: { width: 1.2, color: "#67aee8" } },
      ] : []),
    ]
    : [
      { name: "K线", type: "candlestick" as const, data: bars.map((bar) => [bar.open, bar.close, bar.low, bar.high]), itemStyle: { color: "#e36d62", color0: "#27b78b", borderColor: "#e36d62", borderColor0: "#27b78b" } },
      { name: "SMA20", type: "line" as const, data: sma(20), showSymbol: false, lineStyle: { width: 1.3, color: "#efba63" } },
      { name: "SMA60", type: "line" as const, data: sma(60), showSymbol: false, lineStyle: { width: 1.3, color: "#67aee8" } },
    ];
  const legend = trendChart
    ? showMovingAverages ? ["价格", "SMA20", "SMA60"] : ["价格"]
    : ["K线", "SMA20", "SMA60"];
  chart.setOption({
    animation: false,
    grid: { left: 48, right: 18, top: 28, bottom: 32 },
    tooltip: { trigger: "axis", axisPointer: { type: "cross" }, backgroundColor: "#182321", borderColor: "#2a3b37", textStyle: { color: "#e4efeb" } },
    legend: { top: 0, right: 8, textStyle: { color: "#85948e", fontSize: 11 }, data: legend },
    xAxis: { type: "category", data: dates, boundaryGap: !trendChart, axisLine: { lineStyle: { color: "#293733" } }, axisLabel: { color: "#788983", hideOverlap: true, fontSize: 10 }, splitLine: { show: false } },
    yAxis: { scale: true, splitNumber: 5, axisLabel: { color: "#788983", fontSize: 10 }, splitLine: { lineStyle: { color: "#1e2a27" } }, axisLine: { show: false } },
    dataZoom: [{ type: "inside", start: trendChart ? 0 : 65, end: 100 }, { type: "slider", height: 14, bottom: 1, borderColor: "#24322e", fillerColor: "rgba(39, 191, 144, .11)", textStyle: { color: "#687a73" } }],
    series,
  });
}

/** Resize the chart with its panel and release the canvas on unmount. */
function resizeChart(): void { chart?.resize(); }

watch(() => [props.bars, props.timeframe], drawChart, { deep: true });
onMounted(() => {
  drawChart();
  window.addEventListener("resize", resizeChart);
});
onUnmounted(() => {
  window.removeEventListener("resize", resizeChart);
  chart?.dispose();
});
</script>

<template><div ref="element" class="market-chart"></div></template>
