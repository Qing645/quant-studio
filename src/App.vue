<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from "vue";
import * as echarts from "echarts/core";
import { CandlestickChart, LineChart } from "echarts/charts";
import { DataZoomComponent, GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import {
  Activity, Bot, ChartCandlestick, ChevronDown, CircleHelp, Database,
  FlaskConical, LayoutDashboard, LoaderCircle, Plus, RefreshCw, Save,
  Settings2, ShieldCheck, Sparkles, Wifi,
} from "@lucide/vue";

type View = "market" | "strategy" | "backtest" | "ai";
type Timeframe = "intraday" | "five_day" | "daily" | "weekly" | "monthly";
interface Bar { datetime: string; open: number; high: number; low: number; close: number; volume: number }
interface Analysis {
  price: number; changePct: number; sma20: number; sma60: number; macd: number;
  rsi14: number; atr20: number; trend: boolean; momentum: boolean;
  volumeExpansion: boolean; breakout: boolean; signal: string; assessment: string;
}
interface MarketSnapshot {
  symbol: string; timeframe: Timeframe; metadata: { source: string; sourceTime: string; exchange: string; sampled: boolean; marketOpen: boolean; sessionStatus: string; delayNotice: string; analysisBasis: string; dayChangePct: number | null };
  analysis: Analysis; bars: Bar[];
}
interface Strategy { id: string; name: string; fastPeriod: number; slowPeriod: number; description: string; version: number; updatedAt: string }
interface BacktestResult {
  initialCapital: number; finalEquity: number; totalReturnPct: number; annualizedReturnPct: number;
  maxDrawdownPct: number; sharpe: number; winRatePct: number; tradeCount: number;
  openPosition: boolean; equityCurve: { time: string; value: number }[];
  trades: { entry: string; exit: string; returnPct: number; pnl: number }[]; caveat: string;
}

const activeView = ref<View>("market");
const timeframe = ref<Timeframe>("daily");
const symbol = ref("159977");
const timeframeOptions: { value: Timeframe; label: string }[] = [
  { value: "intraday", label: "分时" },
  { value: "five_day", label: "五日" },
  { value: "daily", label: "日K" },
  { value: "weekly", label: "周K" },
  { value: "monthly", label: "月K" },
];
const snapshot = ref<MarketSnapshot | null>(null);
const strategies = ref<Strategy[]>([]);
const loading = ref(false);
const apiReady = ref(false);
const marketStale = ref(false);
const error = ref("");
const chartEl = ref<HTMLDivElement>();
const activeStrategy = reactive({ id: "", name: "双均线趋势", fastPeriod: 20, slowPeriod: 60, description: "快线上穿慢线开仓，快线下穿慢线离场。按下一根K线开盘执行。" });
const backtestForm = reactive({ initialCapital: 100000, feeRate: 0.0003, slippageBps: 5 });
const backtestResult = ref<BacktestResult | null>(null);
const aiSettings = reactive({ baseUrl: "https://api.openai.com/v1", model: "gpt-4o-mini", apiKey: "", hasApiKey: false });
const aiResult = ref("");
const aiBusy = ref(false);
const aiMessage = ref("");
let chart: echarts.ECharts | undefined;
let refreshTimer: number | undefined;
let refreshQueued = false;

const navItems = [
  { id: "market" as const, label: "实时分析", icon: LayoutDashboard },
  { id: "strategy" as const, label: "策略研究", icon: Activity },
  { id: "backtest" as const, label: "策略回测", icon: FlaskConical },
  { id: "ai" as const, label: "AI 模型", icon: Bot },
];
echarts.use([CandlestickChart, LineChart, DataZoomComponent, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

const pageTitle = computed(() => navItems.find((item) => item.id === activeView.value)?.label ?? "实时分析");
const timeframeLabel = computed(() => timeframeOptions.find((item) => item.value === timeframe.value)?.label ?? "日K");
const dayChangePct = computed(() => snapshot.value?.metadata.dayChangePct ?? null);
const selectedExchange = computed(() => {
  const value = symbol.value.trim().toUpperCase();
  if (value.endsWith(".SH") || value.endsWith(".SSE")) return "SH";
  if (value.endsWith(".SZ") || value.endsWith(".SZSE")) return "SZ";
  return /^[569]/.test(value) ? "SH" : "SZ";
});
const isLineTimeframe = computed(() => timeframe.value === "intraday" || timeframe.value === "five_day");
const freshness = computed(() => snapshot.value?.metadata.sampled ? "分钟级公开行情" : "复权K线");

/** Send one JSON request to the local research API and surface its error detail. */
async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`http://127.0.0.1:8765${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...options?.headers },
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.detail ?? `请求失败 (${response.status})`);
  return payload as T;
}

/** Load the selected symbol and render fresh values without hiding fetch errors. */
async function loadMarket(): Promise<void> {
  if (loading.value) {
    refreshQueued = true;
    return;
  }
  loading.value = true;
  error.value = "";
  const requestedSymbol = symbol.value.trim().toUpperCase().replace(/\.(SZSE|SSE|SZ|SH)$/, "");
  if (snapshot.value && snapshot.value.symbol !== requestedSymbol) {
    snapshot.value = null;
    aiResult.value = "";
    backtestResult.value = null;
  }
  try {
    snapshot.value = await api<MarketSnapshot>(`/api/market?symbol=${encodeURIComponent(symbol.value)}&timeframe=${timeframe.value}`);
    marketStale.value = false;
    apiReady.value = true;
    await nextTick();
    void drawMarketChart();
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "行情读取失败";
    marketStale.value = Boolean(snapshot.value);
    apiReady.value = true;
  } finally {
    loading.value = false;
    if (refreshQueued) {
      refreshQueued = false;
      void loadMarket();
    }
  }
}

/** Draw intraday price curves or adjusted candlesticks with the same moving averages. */
async function drawMarketChart(): Promise<void> {
  if (!chartEl.value || !snapshot.value || activeView.value !== "market") return;
  chart?.dispose();
  chart = echarts.init(chartEl.value);
  const bars = snapshot.value.bars;
  const trendChart = isLineTimeframe.value;
  const showMovingAverages = timeframe.value !== "intraday";
  const dates = bars.map((bar) => trendChart ? bar.datetime.slice(5, 16) : bar.datetime.slice(0, 10));
  const candles = bars.map((bar) => [bar.open, bar.close, bar.low, bar.high]);
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
      { name: "K线", type: "candlestick" as const, data: candles, itemStyle: { color: "#e36d62", color0: "#27b78b", borderColor: "#e36d62", borderColor0: "#27b78b" } },
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

/** Read locally versioned strategy definitions from the API. */
async function loadStrategies(): Promise<void> {
  try {
    strategies.value = await api<Strategy[]>("/api/strategies");
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "策略读取失败";
  }
}

/** Save the editable rule set as a new strategy version. */
async function saveStrategy(): Promise<void> {
  try {
    const saved = await api<Strategy>("/api/strategies", { method: "POST", body: JSON.stringify(activeStrategy) });
    activeStrategy.id = saved.id;
    await loadStrategies();
    error.value = "策略已保存为 v" + saved.version;
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "保存策略失败";
  }
}

/** Run the selected rule set with the current fee and slippage inputs. */
async function runBacktest(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    backtestResult.value = await api<BacktestResult>("/api/backtest", {
      method: "POST",
      body: JSON.stringify({ symbol: symbol.value, timeframe: timeframe.value, ...backtestForm, fastPeriod: activeStrategy.fastPeriod, slowPeriod: activeStrategy.slowPeriod }),
    });
    await nextTick();
    void drawEquityChart();
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "回测失败";
  } finally {
    loading.value = false;
  }
}

/** Render the backtest's marked-to-market equity curve. */
async function drawEquityChart(): Promise<void> {
  if (!chartEl.value || !backtestResult.value || activeView.value !== "backtest") return;
  chart?.dispose();
  chart = echarts.init(chartEl.value);
  const points = backtestResult.value.equityCurve;
  chart.setOption({
    animation: false,
    grid: { left: 58, right: 20, top: 20, bottom: 34 },
    tooltip: { trigger: "axis", backgroundColor: "#182321", borderColor: "#2a3b37", textStyle: { color: "#e4efeb" } },
    xAxis: { type: "category", data: points.map((item) => item.time.slice(0, 10)), axisLabel: { color: "#788983", hideOverlap: true }, axisLine: { lineStyle: { color: "#293733" } } },
    yAxis: { type: "value", scale: true, axisLabel: { color: "#788983" }, splitLine: { lineStyle: { color: "#1e2a27" } } },
    series: [{ name: "账户权益", type: "line", data: points.map((item) => +item.value.toFixed(2)), showSymbol: false, lineStyle: { color: "#49cba0", width: 2 }, areaStyle: { color: "rgba(51, 192, 145, .08)" } }],
  });
}

/** Load model metadata without retrieving or exposing the saved secret. */
async function loadAISettings(): Promise<void> {
  try {
    const settings = await api<{ baseUrl?: string; model?: string; hasApiKey: boolean }>("/api/ai/settings");
    if (settings.baseUrl) aiSettings.baseUrl = settings.baseUrl;
    if (settings.model) aiSettings.model = settings.model;
    aiSettings.hasApiKey = settings.hasApiKey;
  } catch { /* Settings remain editable while the backend is offline. */ }
}

/** Persist model metadata and send any new key directly to the local keychain API. */
async function saveAISettings(): Promise<void> {
  aiMessage.value = "正在保存模型配置";
  try {
    const result = await api<{ hasApiKey: boolean }>("/api/ai/settings", { method: "PUT", body: JSON.stringify(aiSettings) });
    aiSettings.hasApiKey = result.hasApiKey;
    aiSettings.apiKey = "";
    aiMessage.value = "配置已保存，API Key 存在系统钥匙串";
  } catch (cause) {
    aiMessage.value = cause instanceof Error ? cause.message : "配置保存失败";
  }
}

/** Make a small request to verify the configured model connection. */
async function testAI(): Promise<void> {
  aiBusy.value = true;
  aiMessage.value = "正在测试模型连接";
  try {
    await api("/api/ai/test", { method: "POST" });
    aiMessage.value = "模型连接正常";
  } catch (cause) {
    aiMessage.value = cause instanceof Error ? cause.message : "模型连接失败";
  } finally {
    aiBusy.value = false;
  }
}

/** Request a grounded explanation using only the latest displayed indicators. */
async function askAI(): Promise<void> {
  if (!snapshot.value) return;
  aiBusy.value = true;
  aiResult.value = "";
  try {
    const result = await api<{ analysis: string }>("/api/ai/analyze", {
      method: "POST",
      body: JSON.stringify({ symbol: snapshot.value.symbol, timeframe: timeframe.value, sourceTime: snapshot.value.metadata.sourceTime, analysisBasis: snapshot.value.metadata.analysisBasis, analysis: snapshot.value.analysis }),
    });
    aiResult.value = result.analysis;
  } catch (cause) {
    aiResult.value = cause instanceof Error ? cause.message : "AI 分析失败";
  } finally {
    aiBusy.value = false;
  }
}

/** Copy a saved strategy into the editor for review or modification. */
function selectStrategy(strategy: Strategy): void {
  activeStrategy.id = strategy.id;
  activeStrategy.name = strategy.name;
  activeStrategy.fastPeriod = strategy.fastPeriod;
  activeStrategy.slowPeriod = strategy.slowPeriod;
  activeStrategy.description = strategy.description;
}

watch(timeframe, () => {
  aiResult.value = "";
  backtestResult.value = null;
  void loadMarket();
});
watch(activeView, async (view) => {
  if (view === "market" && snapshot.value) { await nextTick(); void drawMarketChart(); }
  if (view === "backtest" && backtestResult.value) { await nextTick(); void drawEquityChart(); }
});
onMounted(async () => {
  try { await api("/api/health"); apiReady.value = true; } catch { apiReady.value = false; }
  await Promise.all([loadStrategies(), loadAISettings(), loadMarket()]);
  refreshTimer = window.setInterval(() => { if (activeView.value === "market") void loadMarket(); }, 60_000);
  window.addEventListener("resize", resizeChart);
});
onUnmounted(() => {
  if (refreshTimer) window.clearInterval(refreshTimer);
  window.removeEventListener("resize", resizeChart);
  chart?.dispose();
});
/** Keep the active chart fitted to its responsive container. */
function resizeChart(): void { chart?.resize(); }
</script>

<template>
  <main class="shell">
    <aside class="sidebar">
      <div class="brand"><span class="brand-mark"><Activity :size="18" /></span><span><b>QUANT</b><small>RESEARCH STUDIO</small></span></div>
      <div class="workspace-label">工作区</div>
      <nav aria-label="主导航">
        <button v-for="item in navItems" :key="item.id" class="nav-item" :class="{ active: activeView === item.id }" @click="activeView = item.id">
          <component :is="item.icon" :size="17" :stroke-width="1.8" /><span>{{ item.label }}</span><span v-if="item.id === 'market' && activeView === item.id" class="nav-live"></span>
        </button>
      </nav>
      <div class="sidebar-bottom">
        <div class="data-state"><span class="state-dot" :class="{ offline: !apiReady }"></span><span>{{ apiReady ? "本地研究环境" : "连接本地服务中" }}</span></div>
        <div class="sidebar-foot"><ShieldCheck :size="14" /> 研究模式 · 不连接交易账户</div>
      </div>
    </aside>

    <section class="main-area">
      <header class="topbar">
        <div class="breadcrumb"><span>研究工作台</span><span class="crumb-slash">/</span><strong>{{ pageTitle }}</strong></div>
        <div class="top-actions">
          <div class="connection"><span class="state-dot" :class="{ offline: !snapshot || marketStale || !snapshot.metadata.marketOpen }"></span>{{ marketStale ? "刷新失败 · 显示最近数据" : snapshot ? snapshot.metadata.sessionStatus : "等待行情源" }}</div>
          <button class="icon-button" title="刷新行情" aria-label="刷新行情" :disabled="loading" @click="loadMarket"><RefreshCw :size="16" :class="{ spinning: loading }" /></button>
          <span class="avatar">QS</span>
        </div>
      </header>

      <div class="content">
        <div v-if="error" class="notice-bar"><CircleHelp :size="15" /><span>{{ error }}</span><button @click="error = ''" aria-label="关闭提示">×</button></div>

        <template v-if="activeView === 'market'">
          <div class="page-heading">
            <div><div class="eyebrow">MARKET MONITOR <span class="eyebrow-line"></span> {{ freshness }}</div><h1>实时策略分析</h1><p>行情、趋势结构与策略条件在同一视图追踪。</p></div>
            <div class="heading-controls">
              <label class="symbol-input"><span>标的代码</span><input v-model="symbol" maxlength="11" placeholder="159977" aria-label="输入六位股票或 ETF 代码" @keydown.enter="loadMarket" /><span class="exchange">{{ selectedExchange }}</span></label>
              <div class="segmented" role="group" aria-label="行情周期">
                <button v-for="item in timeframeOptions" :key="item.value" :class="{ selected: timeframe === item.value }" :aria-pressed="timeframe === item.value" @click="timeframe = item.value">{{ item.label }}</button>
              </div>
              <button class="primary-button" :disabled="loading" @click="loadMarket"><RefreshCw :size="15" /> 查询行情</button>
            </div>
          </div>

          <div v-if="snapshot" class="quote-strip">
            <div class="instrument"><span class="instrument-code">{{ snapshot.symbol }}<i>.{{ snapshot.metadata.exchange }}</i></span><span class="instrument-name">A股 / ETF · 公共行情</span></div>
            <div class="quote-price"><strong :class="dayChangePct === null ? '' : dayChangePct >= 0 ? 'up' : 'down'">{{ snapshot.analysis.price.toFixed(3) }}</strong><span :class="dayChangePct === null ? '' : dayChangePct >= 0 ? 'up' : 'down'">{{ dayChangePct === null ? "—" : `${dayChangePct >= 0 ? '+' : ''}${dayChangePct.toFixed(2)}%` }}</span></div>
            <div class="quote-meta"><span>{{ marketStale || !snapshot.metadata.marketOpen ? "最近有效行情" : "数据时间" }}</span><b>{{ snapshot.metadata.sourceTime }}</b></div>
            <div class="quote-meta"><span>数据来源</span><b><Wifi :size="13" /> {{ snapshot.metadata.source }}</b></div>
            <div class="quote-meta"><span>自动刷新</span><b>60 秒</b></div>
          </div>
          <div v-else class="quote-strip quote-empty"><LoaderCircle :size="17" class="spinning" /> {{ loading ? "正在加载公开行情" : "行情暂不可用" }}</div>

          <section class="market-layout">
            <div class="chart-section">
              <div class="section-head"><div><h2>价格结构</h2><span>{{ timeframe === 'intraday' ? "当日分时价格" : isLineTimeframe ? "分钟走势 · SMA20 · SMA60" : "OHLC · SMA20 · SMA60" }}</span></div><div class="legend-inline"><i class="legend-up"></i>上涨 <i class="legend-down"></i>下跌 <span>{{ timeframeLabel }} · {{ isLineTimeframe ? "分钟走势" : "复权K线" }}</span></div></div>
              <div ref="chartEl" class="market-chart"></div>
              <div class="chart-caption"><Database :size="13" /> {{ snapshot ? `${snapshot.metadata.sessionStatus} · ${snapshot.metadata.delayNotice}` : "等待数据" }}</div>
            </div>

            <aside class="analysis-panel">
              <div class="section-head"><div><h2>策略判断</h2><span>规则条件 · {{ snapshot?.metadata.analysisBasis ?? "只读模拟" }}</span></div><span class="signal-tag" :class="snapshot?.analysis.signal === '满足入场条件' ? 'signal-positive' : ''">{{ snapshot?.analysis.signal ?? "等待数据" }}</span></div>
              <div v-if="snapshot" class="condition-list">
                <div class="condition-row"><span class="condition-icon" :class="{ pass: snapshot.analysis.trend }"><Activity :size="15" /></span><span>趋势过滤 <small>收盘价相对 SMA60</small></span><b :class="snapshot.analysis.trend ? 'up' : 'muted'">{{ snapshot.analysis.trend ? "满足" : "未满足" }}</b></div>
                <div class="condition-row"><span class="condition-icon" :class="{ pass: snapshot.analysis.momentum }"><ChartCandlestick :size="15" /></span><span>MACD 动能 <small>柱值 {{ snapshot.analysis.macd.toFixed(4) }}</small></span><b :class="snapshot.analysis.momentum ? 'up' : 'muted'">{{ snapshot.analysis.momentum ? "偏强" : "偏弱" }}</b></div>
                <div class="condition-row"><span class="condition-icon" :class="{ pass: snapshot.analysis.volumeExpansion || snapshot.analysis.breakout }"><Database :size="15" /></span><span>量能 / 突破 <small>近 5 根对比 30 根均量</small></span><b :class="snapshot.analysis.volumeExpansion || snapshot.analysis.breakout ? 'up' : 'muted'">{{ snapshot.analysis.volumeExpansion ? "量能放大" : snapshot.analysis.breakout ? "区间突破" : "未触发" }}</b></div>
              </div>
              <div v-if="snapshot" class="metric-grid">
                <div><span>SMA20</span><b>{{ snapshot.analysis.sma20.toFixed(3) }}</b></div><div><span>SMA60</span><b>{{ snapshot.analysis.sma60.toFixed(3) }}</b></div>
                <div><span>RSI14</span><b :class="snapshot.analysis.rsi14 < 30 ? 'amber' : ''">{{ snapshot.analysis.rsi14.toFixed(1) }}</b></div><div><span>ATR20</span><b>{{ snapshot.analysis.atr20.toFixed(3) }}</b></div>
              </div>
              <div v-if="snapshot" class="assessment"><div class="assessment-title"><Sparkles :size="14" /> 信号依据</div><p>{{ snapshot.analysis.assessment }}</p></div>
              <button class="text-action" @click="activeView = 'ai'"><Bot :size="15" /> 让 AI 解读当前数据 <span>→</span></button>
            </aside>
          </section>

          <section class="data-table-section">
            <div class="section-head"><div><h2>最近行情</h2><span>{{ snapshot?.bars.length ?? 0 }} 根 {{ timeframeLabel }} 数据</span></div><button class="subtle-button" @click="activeView = 'backtest'">进入回测研究 <span>→</span></button></div>
            <div class="table-wrap"><table><thead><tr><th>时间</th><template v-if="isLineTimeframe"><th>价格</th></template><template v-else><th>开盘</th><th>最高</th><th>最低</th><th>收盘</th></template><th class="align-right">成交量</th></tr></thead><tbody><tr v-for="bar in snapshot?.bars.slice(-12).reverse() ?? []" :key="bar.datetime"><td>{{ bar.datetime }}</td><template v-if="isLineTimeframe"><td>{{ bar.close.toFixed(3) }}</td></template><template v-else><td>{{ bar.open.toFixed(3) }}</td><td class="up">{{ bar.high.toFixed(3) }}</td><td class="down">{{ bar.low.toFixed(3) }}</td><td>{{ bar.close.toFixed(3) }}</td></template><td class="align-right">{{ Math.round(bar.volume).toLocaleString() }}</td></tr></tbody></table></div>
          </section>
        </template>

        <template v-else-if="activeView === 'strategy'">
          <div class="page-heading"><div><div class="eyebrow">STRATEGY LAB <span class="eyebrow-line"></span> VERSIONED RULES</div><h1>策略研究</h1><p>用可读规则定义信号，保存版本并复用于回测。</p></div><button class="primary-button" @click="activeStrategy.id = ''; activeStrategy.name = '新策略'; activeStrategy.fastPeriod = 20; activeStrategy.slowPeriod = 60; activeStrategy.description = ''"><Plus :size="15" /> 新建策略</button></div>
          <div class="research-layout">
            <aside class="strategy-list"><div class="list-title">已保存策略 <span>{{ strategies.length }}</span></div><button v-for="strategy in strategies" :key="strategy.id" class="strategy-list-item" :class="{ chosen: activeStrategy.id === strategy.id }" @click="selectStrategy(strategy)"><span class="strategy-symbol"><Activity :size="15" /></span><span><b>{{ strategy.name }}</b><small>双均线 · v{{ strategy.version }}</small></span><ChevronDown :size="14" /></button><div v-if="!strategies.length" class="empty-list">还没有保存的策略</div></aside>
            <section class="editor-section"><div class="section-head"><div><h2>规则编辑器</h2><span>策略逻辑将在下一根K线执行</span></div><span class="draft-tag">研究草稿</span></div>
              <div class="form-grid"><label class="field full"><span>策略名称</span><input v-model="activeStrategy.name" maxlength="60" /></label><label class="field"><span>快线周期</span><div class="number-field"><input v-model.number="activeStrategy.fastPeriod" type="number" min="2" max="100" /><span>根K线 SMA</span></div></label><label class="field"><span>慢线周期</span><div class="number-field"><input v-model.number="activeStrategy.slowPeriod" type="number" min="3" max="120" /><span>根K线 SMA</span></div></label><label class="field full"><span>策略说明</span><textarea v-model="activeStrategy.description" rows="3" maxlength="500"></textarea></label></div>
              <div class="rule-preview"><div class="preview-head"><Settings2 :size="15" /> 规则预览</div><div><span class="rule-token">开仓</span> SMA{{ activeStrategy.fastPeriod }} 上穿 SMA{{ activeStrategy.slowPeriod }}</div><div><span class="rule-token exit">离场</span> SMA{{ activeStrategy.fastPeriod }} 下穿 SMA{{ activeStrategy.slowPeriod }}</div><small>以信号后的下一根K线开盘价模拟成交，计入手续费和滑点。</small></div>
              <div class="form-actions"><button class="primary-button" :disabled="activeStrategy.fastPeriod >= activeStrategy.slowPeriod" @click="saveStrategy"><Save :size="15" /> 保存策略版本</button><button class="subtle-button" @click="activeView = 'backtest'">使用此策略回测 <span>→</span></button></div>
            </section>
          </div>
        </template>

        <template v-else-if="activeView === 'backtest'">
          <div class="page-heading"><div><div class="eyebrow">HISTORICAL SIMULATION <span class="eyebrow-line"></span> NEXT BAR EXECUTION</div><h1>策略回测</h1><p>成本可配置，信号后移执行，避免同K线前视。</p></div><button class="primary-button" :disabled="loading" @click="runBacktest"><LoaderCircle v-if="loading" :size="15" class="spinning" /><FlaskConical v-else :size="15" /> 运行回测</button></div>
          <section class="backtest-config"><div class="config-heading"><div><h2>{{ activeStrategy.name }}</h2><span>标的 {{ symbol }} · {{ timeframeLabel }} · 腾讯公开历史样本</span></div><button class="subtle-button" @click="activeView = 'strategy'">修改策略 <span>→</span></button></div><div class="config-fields"><label class="field"><span>初始资金</span><div class="number-field"><input v-model.number="backtestForm.initialCapital" type="number" min="1000" step="10000" /><span>元</span></div></label><label class="field"><span>手续费率</span><div class="number-field"><input v-model.number="backtestForm.feeRate" type="number" min="0" max="0.02" step="0.0001" /><span>单边</span></div></label><label class="field"><span>滑点</span><div class="number-field"><input v-model.number="backtestForm.slippageBps" type="number" min="0" max="500" step="1" /><span>基点</span></div></label><div class="config-note"><ShieldCheck :size="15" /> 研究用途 · 不会连接交易账户</div></div></section>
          <template v-if="backtestResult"><div class="results-toolbar"><div><h2>回测结果</h2><span>{{ backtestResult.tradeCount }} 笔已平仓 · {{ backtestResult.openPosition ? '期末持仓' : '期末空仓' }}</span></div><span class="data-range">数据截至 {{ snapshot?.metadata.sourceTime ?? "最近可用" }}</span></div><div class="result-metrics"><div><span>累计收益</span><b :class="backtestResult.totalReturnPct >= 0 ? 'up' : 'down'">{{ backtestResult.totalReturnPct.toFixed(2) }}%</b></div><div><span>年化收益*</span><b>{{ backtestResult.annualizedReturnPct.toFixed(2) }}%</b></div><div><span>最大回撤</span><b class="down">{{ backtestResult.maxDrawdownPct.toFixed(2) }}%</b></div><div><span>夏普比率</span><b>{{ backtestResult.sharpe.toFixed(2) }}</b></div><div><span>胜率</span><b>{{ backtestResult.winRatePct.toFixed(1) }}%</b></div></div><div class="chart-section result-chart-section"><div class="section-head"><div><h2>账户权益</h2><span>按收盘盯市 · 未平仓盈亏计入期末净值</span></div></div><div ref="chartEl" class="equity-chart"></div></div><div class="risk-note"><CircleHelp :size="15" /> {{ backtestResult.caveat }} 年化指标仅作参考，分钟样本不可直接与日线年化比较。</div><div v-if="backtestResult.trades.length" class="table-wrap trades-table"><table><thead><tr><th>开仓时间</th><th>平仓时间</th><th>收益率</th><th class="align-right">盈亏</th></tr></thead><tbody><tr v-for="(trade, index) in backtestResult.trades.slice(-20).reverse()" :key="index"><td>{{ trade.entry.slice(0, 16) }}</td><td>{{ trade.exit.slice(0, 16) }}</td><td :class="trade.returnPct >= 0 ? 'up' : 'down'">{{ trade.returnPct.toFixed(2) }}%</td><td class="align-right">{{ trade.pnl.toFixed(2) }}</td></tr></tbody></table></div></template>
          <div v-else class="empty-results"><FlaskConical :size="22" /><b>运行策略以生成结果</b><span>回测区间由数据源可提供的历史K线决定。</span></div>
        </template>

        <template v-else>
          <div class="page-heading"><div><div class="eyebrow">MODEL CONNECTION <span class="eyebrow-line"></span> OPENAI-COMPATIBLE</div><h1>AI 模型</h1><p>配置模型连接，用于解释行情信号和回测结果。</p></div><span class="secure-badge"><ShieldCheck :size="15" /> API Key 安全存储</span></div>
          <section class="ai-layout"><div class="ai-config-panel"><div class="section-head"><div><h2>服务连接</h2><span>兼容 OpenAI Chat Completions 的接口</span></div><span class="connection-state" :class="{ ready: aiSettings.hasApiKey }"><span class="state-dot"></span>{{ aiSettings.hasApiKey ? "已配置密钥" : "未配置密钥" }}</span></div><div class="form-grid"><label class="field full"><span>API Base URL</span><input v-model="aiSettings.baseUrl" placeholder="https://api.openai.com/v1" /></label><label class="field full"><span>模型名称</span><input v-model="aiSettings.model" placeholder="gpt-4o-mini" /></label><label class="field full"><span>API Key <small>新密钥保存后不会再次显示</small></span><input v-model="aiSettings.apiKey" type="password" autocomplete="new-password" :placeholder="aiSettings.hasApiKey ? '已保存到系统钥匙串，留空则保持不变' : '输入服务商 API Key'" /></label></div><div class="ai-actions"><button class="primary-button" @click="saveAISettings"><Save :size="15" /> 保存配置</button><button class="subtle-button" :disabled="aiBusy" @click="testAI">{{ aiBusy ? "测试中…" : "测试连接" }}</button><span class="inline-message">{{ aiMessage }}</span></div><div class="privacy-note"><ShieldCheck :size="15" /> Key 由本地后端写入 macOS 钥匙串；行情分析请求只发送指标摘要，不发送账户或交易凭证。</div></div>
            <aside class="ai-analysis-panel"><div class="section-head"><div><h2>当前行情解读</h2><span>{{ snapshot ? `${snapshot.symbol} · ${snapshot.metadata.sourceTime}` : "尚无可分析行情" }}</span></div><Sparkles :size="17" class="accent-icon" /></div><p class="ai-summary">AI 将收到当前指标、策略条件、标的周期和行情时间，并只基于这些数据解释趋势、动能和风险边界。</p><button class="primary-button wide-button" :disabled="aiBusy || !snapshot" @click="askAI"><LoaderCircle v-if="aiBusy" :size="15" class="spinning" /><Sparkles v-else :size="15" /> 生成数据分析</button><div v-if="aiResult" class="ai-result"><div class="assessment-title"><Bot :size="14" /> 模型分析</div><p>{{ aiResult }}</p></div><div v-else class="ai-result-placeholder">分析结果会显示在这里，并保留本次使用的数据时间。</div></aside></section>
        </template>
      </div>
      <footer class="app-footer"><span><span class="state-dot"></span> 本地研究环境</span><span>市场数据仅用于研究 · 不构成投资建议</span><span>Quant Studio 0.1.0</span></footer>
    </section>
  </main>
</template>
