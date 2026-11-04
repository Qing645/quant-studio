<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from "vue";
import * as echarts from "echarts/core";
import { LineChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { BacktestResult } from "../types";

const props = defineProps<{ result: BacktestResult }>();
const element = ref<HTMLDivElement>();
let chart: echarts.ECharts | undefined;

echarts.use([LineChart, GridComponent, TooltipComponent, CanvasRenderer]);

/** Draw the marked-to-market equity curve for the latest backtest. */
function drawChart(): void {
  if (!element.value) return;
  chart?.dispose();
  chart = echarts.init(element.value);
  const points = props.result.equityCurve;
  chart.setOption({
    animation: false,
    grid: { left: 58, right: 20, top: 20, bottom: 34 },
    tooltip: { trigger: "axis", backgroundColor: "#182321", borderColor: "#2a3b37", textStyle: { color: "#e4efeb" } },
    legend: props.result.benchmark ? { top: 0, right: 8, textStyle: { color: "#85948e", fontSize: 11 } } : undefined,
    xAxis: { type: "category", data: points.map((item) => item.time.slice(0, 10)), axisLabel: { color: "#788983", hideOverlap: true }, axisLine: { lineStyle: { color: "#293733" } } },
    yAxis: { type: "value", scale: true, axisLabel: { color: "#788983" }, splitLine: { lineStyle: { color: "#1e2a27" } } },
    series: [
      { name: "策略组合", type: "line", data: points.map((item) => +item.value.toFixed(2)), showSymbol: false, lineStyle: { color: "#49cba0", width: 2 }, areaStyle: { color: "rgba(51, 192, 145, .08)" } },
      ...(props.result.benchmark ? [{ name: props.result.benchmark.name, type: "line" as const, data: props.result.benchmark.equityCurve.map((item) => +item.value.toFixed(2)), showSymbol: false, lineStyle: { color: "#efba63", width: 1.5, type: "dashed" as const } }] : []),
    ],
  });
}

/** Keep the chart fitted to its panel and release its canvas on unmount. */
function resizeChart(): void { chart?.resize(); }

watch(() => props.result, drawChart, { deep: true });
onMounted(() => {
  drawChart();
  window.addEventListener("resize", resizeChart);
});
onUnmounted(() => {
  window.removeEventListener("resize", resizeChart);
  chart?.dispose();
});
</script>

<template><div ref="element" class="equity-chart"></div></template>
