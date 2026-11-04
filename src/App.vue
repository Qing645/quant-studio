<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from "vue";
import {
  Activity,
  Bot,
  CircleHelp,
  FlaskConical,
  LayoutDashboard,
  RefreshCw,
  ShieldCheck
} from "@lucide/vue";
import AIView from "./components/AIView.vue";
import BacktestView from "./components/BacktestView.vue";
import MarketView from "./components/MarketView.vue";
import StrategyView from "./components/StrategyView.vue";
import { api } from "./services/api";
import type {
  AISettings,
  BacktestResult,
  MarketSnapshot,
  Strategy,
  StrategyTemplate,
  StrategyType,
  Timeframe,
  TimeframeOption,
  View
} from "./types";

const activeView = ref<View>("market");
const timeframe = ref<Timeframe>("daily");
const symbol = ref("159977");
const mlSymbols = ref("159977");
const timeframeOptions: TimeframeOption[] = [
  { value: "intraday", label: "分时" },
  { value: "five_day", label: "五日" },
  { value: "daily", label: "日K" },
  { value: "weekly", label: "周K" },
  { value: "monthly", label: "月K" }
];
const navItems = [
  { id: "market" as const, label: "实时分析", icon: LayoutDashboard },
  { id: "strategy" as const, label: "策略研究", icon: Activity },
  { id: "backtest" as const, label: "策略回测", icon: FlaskConical },
  { id: "ai" as const, label: "AI 模型", icon: Bot }
];

const snapshot = ref<MarketSnapshot | null>(null);
const strategies = ref<Strategy[]>([]);
const strategyTemplates = ref<StrategyTemplate[]>([]);
const strategiesLoading = ref(false);
const strategySaving = ref(false);
const loading = ref(false);
const apiReady = ref(false);
const marketStale = ref(false);
const error = ref("");
const activeStrategy = reactive<Strategy>({
  id: "",
  name: "",
  strategyType: "trend_filter",
  fastPeriod: 20,
  slowPeriod: 60,
  description: "",
  version: 0,
  updatedAt: ""
});
const backtestForm = reactive({
  initialCapital: 100000,
  feeRate: 0.0003,
  slippageBps: 5
});
const backtestResult = ref<BacktestResult | null>(null);
const aiSettings = reactive<AISettings>({
  baseUrl: "https://api.openai.com/v1",
  model: "gpt-4o-mini",
  apiKey: "",
  hasApiKey: false
});
const aiResult = ref("");
const aiBusy = ref(false);
const aiMessage = ref("");
let refreshTimer: number | undefined;
let refreshQueued = false;

const pageTitle = computed(
  () =>
    navItems.find((item) => item.id === activeView.value)?.label ?? "实时分析"
);
const timeframeLabel = computed(
  () =>
    timeframeOptions.find((item) => item.value === timeframe.value)?.label ??
    "日K"
);
const activeTemplate = computed(() =>
  strategyTemplates.value.find(
    (item) => item.strategyType === activeStrategy.strategyType
  )
);
const strategyTypeLabel = computed(() => activeTemplate.value?.label ?? "");
const backtestRequest = computed(() => ({
  symbol: symbol.value,
  symbols:
    activeStrategy.strategyType === "ml_direction"
      ? mlSymbols.value
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean)
      : [],
  timeframe: timeframe.value,
  strategyType: activeStrategy.strategyType,
  ...backtestForm,
  fastPeriod: activeStrategy.fastPeriod,
  slowPeriod: activeStrategy.slowPeriod
}));
const backtestContext = computed(() => JSON.stringify(backtestRequest.value));
const strategyChanged = computed(() => {
  const saved = strategies.value.find((item) => item.id === activeStrategy.id);
  return (
    !saved || strategyDefinition(saved) !== strategyDefinition(activeStrategy)
  );
});
const dayChangePct = computed(
  () => snapshot.value?.metadata.dayChangePct ?? null
);
const selectedExchange = computed(() => {
  const value = symbol.value.trim().toUpperCase();
  if (value.endsWith(".SH") || value.endsWith(".SSE")) return "SH";
  if (value.endsWith(".SZ") || value.endsWith(".SZSE")) return "SZ";
  return /^[569]/.test(value) ? "SH" : "SZ";
});
const freshness = computed(() =>
  snapshot.value?.metadata.sampled ? "分钟级公开行情" : "复权K线"
);

/** Load the selected symbol and queue one refresh if a request is already running. */
async function loadMarket(): Promise<void> {
  if (loading.value) {
    refreshQueued = true;
    return;
  }
  loading.value = true;
  error.value = "";
  const strategyQuery = new URLSearchParams({
    strategyType: activeStrategy.strategyType,
    fastPeriod: String(activeStrategy.fastPeriod),
    slowPeriod: String(activeStrategy.slowPeriod)
  });
  const requestedSymbol = symbol.value
    .trim()
    .toUpperCase()
    .replace(/\.(SZSE|SSE|SZ|SH)$/, "");
  if (snapshot.value && snapshot.value.symbol !== requestedSymbol) {
    snapshot.value = null;
    aiResult.value = "";
    backtestResult.value = null;
  }
  try {
    snapshot.value = await api<MarketSnapshot>(
      `/api/market?symbol=${encodeURIComponent(symbol.value)}&timeframe=${timeframe.value}&${strategyQuery}`
    );
    marketStale.value = false;
    apiReady.value = true;
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

/** Select an existing saved rule set for live evaluation and shared backtesting. */
function selectLiveStrategy(id: string): void {
  const strategy = strategies.value.find((item) => item.id === id);
  if (strategy) selectStrategy(strategy);
  else if (!id) startNewStrategy();
  aiResult.value = "";
}

/** Read locally versioned strategy definitions from the API. */
async function loadStrategies(): Promise<void> {
  strategiesLoading.value = true;
  try {
    strategies.value = await api<Strategy[]>("/api/strategies");
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "策略读取失败";
  } finally {
    strategiesLoading.value = false;
  }
}

/** Load server-owned calculation methods without replacing the working strategy. */
async function loadStrategyTemplates(): Promise<void> {
  try {
    strategyTemplates.value = await api<StrategyTemplate[]>(
      "/api/strategy-templates"
    );
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "策略模板读取失败";
  }
}

/** Restore the most recent saved strategy as soon as its metadata is available. */
async function loadStrategyWorkspace(): Promise<void> {
  await Promise.all([loadStrategyTemplates(), loadStrategies()]);
  if (activeStrategy.name) return;
  if (strategies.value[0]) selectStrategy(strategies.value[0]);
  else startNewStrategy();
}

/** Compare editable fields only so metadata does not create unnecessary revisions. */
function strategyDefinition(strategy: Strategy): string {
  return JSON.stringify([
    strategy.name.trim(),
    strategy.strategyType,
    strategy.fastPeriod,
    strategy.slowPeriod,
    strategy.description
  ]);
}

/** Preserve the selected market unless the calculation method cannot support it. */
function alignStrategyScope(template: StrategyTemplate): void {
  const code = symbol.value
    .trim()
    .toUpperCase()
    .replace(/\.(SZSE|SSE|SZ|SH)$/, "");
  if (
    template.supportedSymbols.length &&
    !template.supportedSymbols.includes(code)
  )
    symbol.value = template.defaultSymbol;
  if (!template.supportedTimeframes.includes(timeframe.value))
    timeframe.value = template.defaultTimeframe;
}

/** Create a draft only through the explicit new-strategy action. */
function startNewStrategy(): void {
  if (strategySaving.value) return;
  const template = activeTemplate.value ?? strategyTemplates.value[0];
  if (!template) return;
  Object.assign(activeStrategy, {
    id: "",
    name: "新策略",
    strategyType: template.strategyType,
    fastPeriod: template.defaultFastPeriod,
    slowPeriod: template.defaultSlowPeriod,
    description: template.description,
    version: 0,
    updatedAt: ""
  });
  alignStrategyScope(template);
  backtestResult.value = null;
}

/** Save once per edit, keeping the server identity and revision on the active record. */
async function saveStrategy(): Promise<void> {
  if (strategySaving.value || !strategyChanged.value || !activeTemplate.value)
    return;
  if (!activeStrategy.name.trim()) {
    error.value = "请输入策略名称";
    return;
  }
  strategySaving.value = true;
  try {
    const saved = await api<Strategy>("/api/strategies", {
      method: "POST",
      body: JSON.stringify({
        ...activeStrategy,
        name: activeStrategy.name.trim()
      })
    });
    Object.assign(activeStrategy, saved);
    strategies.value = [
      saved,
      ...strategies.value.filter((item) => item.id !== saved.id)
    ];
    error.value = "策略已保存为 v" + saved.version;
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "保存策略失败";
  } finally {
    strategySaving.value = false;
  }
}

/** Align a draft with the selected template's supported scope. */
function selectStrategyType(strategyType: StrategyType): void {
  if (strategySaving.value || strategyType === activeStrategy.strategyType)
    return;
  const previous = strategyTemplates.value.find(
    (item) => item.strategyType === activeStrategy.strategyType
  );
  const next = strategyTemplates.value.find(
    (item) => item.strategyType === strategyType
  );
  if (!next) return;
  backtestResult.value = null;
  const useTemplateName =
    !activeStrategy.id &&
    (!activeStrategy.name || activeStrategy.name === previous?.templateName);
  const useTemplateDescription =
    !activeStrategy.description ||
    activeStrategy.description === previous?.description;
  activeStrategy.strategyType = strategyType;
  if (useTemplateName) activeStrategy.name = next.templateName;
  if (useTemplateDescription) activeStrategy.description = next.description;
  activeStrategy.fastPeriod = next.defaultFastPeriod;
  activeStrategy.slowPeriod = next.defaultSlowPeriod;
  alignStrategyScope(next);
}

/** Copy a saved strategy into the editor for review or modification. */
function selectStrategy(strategy: Strategy): void {
  if (strategySaving.value) return;
  backtestResult.value = null;
  Object.assign(activeStrategy, strategy);
  const template = strategyTemplates.value.find(
    (item) => item.strategyType === strategy.strategyType
  );
  if (template) alignStrategyScope(template);
  activeStrategy.strategyType = strategy.strategyType;
}

/** Run the selected rule set with the current fee and slippage inputs. */
async function runBacktest(): Promise<void> {
  loading.value = true;
  error.value = "";
  backtestResult.value = null;
  const requestContext = backtestContext.value;
  try {
    const result = await api<BacktestResult>("/api/backtest", {
      method: "POST",
      body: requestContext
    });
    // An old request must not populate a strategy that was changed while it ran.
    if (requestContext === backtestContext.value) backtestResult.value = result;
  } catch (cause) {
    if (requestContext === backtestContext.value)
      error.value = cause instanceof Error ? cause.message : "回测失败";
  } finally {
    loading.value = false;
  }
}

/** Load model metadata without retrieving or exposing the saved secret. */
async function loadAISettings(): Promise<void> {
  try {
    const settings = await api<{
      baseUrl?: string;
      model?: string;
      hasApiKey: boolean;
    }>("/api/ai/settings");
    if (settings.baseUrl) aiSettings.baseUrl = settings.baseUrl;
    if (settings.model) aiSettings.model = settings.model;
    aiSettings.hasApiKey = settings.hasApiKey;
  } catch {
    /* Settings remain editable while the backend is offline. */
  }
}

/** Persist model metadata and send any new key directly to the local keychain API. */
async function saveAISettings(): Promise<void> {
  aiMessage.value = "正在保存模型配置";
  try {
    const result = await api<{ hasApiKey: boolean }>("/api/ai/settings", {
      method: "PUT",
      body: JSON.stringify(aiSettings)
    });
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

/** Request an explanation using only the latest displayed indicators. */
async function askAI(): Promise<void> {
  if (!snapshot.value) return;
  aiBusy.value = true;
  aiResult.value = "";
  try {
    const result = await api<{ analysis: string }>("/api/ai/analyze", {
      method: "POST",
      body: JSON.stringify({
        symbol: snapshot.value.symbol,
        timeframe: timeframe.value,
        sourceTime: snapshot.value.metadata.sourceTime,
        analysisBasis: snapshot.value.metadata.analysisBasis,
        analysis: snapshot.value.analysis
      })
    });
    aiResult.value = result.analysis;
  } catch (cause) {
    aiResult.value = cause instanceof Error ? cause.message : "AI 分析失败";
  } finally {
    aiBusy.value = false;
  }
}

watch(
  backtestContext,
  () => {
    backtestResult.value = null;
  },
  { flush: "sync" }
);
watch(timeframe, () => {
  aiResult.value = "";
  backtestResult.value = null;
  void loadMarket();
});
watch(
  () => [activeStrategy.strategyType, activeStrategy.fastPeriod, activeStrategy.slowPeriod],
  () => {
    if (activeView.value === "market") void loadMarket();
  }
);
watch(activeView, (view) => {
  if (view === "strategy" && (!strategies.value.length || !strategyTemplates.value.length)) {
    void loadStrategyWorkspace();
  }
});
onMounted(async () => {
  try {
    await api("/api/health");
    apiReady.value = true;
  } catch {
    apiReady.value = false;
  }
  await Promise.all([loadStrategyWorkspace(), loadAISettings(), loadMarket()]);
  refreshTimer = window.setInterval(() => {
    if (activeView.value === "market") void loadMarket();
  }, 60_000);
});
onUnmounted(() => {
  if (refreshTimer) window.clearInterval(refreshTimer);
});
</script>

<template>
  <main class="shell">
    <aside class="sidebar">
      <div class="brand">
        <span class="brand-mark"><Activity :size="18" /></span
        ><span><b>QUANT</b><small>RESEARCH STUDIO</small></span>
      </div>
      <div class="workspace-label">工作区</div>
      <nav aria-label="主导航">
        <button
          v-for="item in navItems"
          :key="item.id"
          class="nav-item"
          :class="{ active: activeView === item.id }"
          @click="activeView = item.id"
        >
          <component :is="item.icon" :size="17" :stroke-width="1.8" /><span>{{
            item.label
          }}</span
          ><span
            v-if="item.id === 'market' && activeView === item.id"
            class="nav-live"
          ></span>
        </button>
      </nav>
      <div class="sidebar-bottom">
        <div class="data-state">
          <span class="state-dot" :class="{ offline: !apiReady }"></span
          ><span>{{ apiReady ? "本地研究环境" : "连接本地服务中" }}</span>
        </div>
        <div class="sidebar-foot">
          <ShieldCheck :size="14" /> 研究模式 · 不连接交易账户
        </div>
      </div>
    </aside>

    <section class="main-area">
      <header class="topbar">
        <div class="breadcrumb">
          <span>研究工作台</span><span class="crumb-slash">/</span
          ><strong>{{ pageTitle }}</strong>
        </div>
        <div class="top-actions">
          <div class="connection">
            <span
              class="state-dot"
              :class="{
                offline:
                  !snapshot || marketStale || !snapshot.metadata.marketOpen
              }"
            ></span
            >{{
              marketStale
                ? "刷新失败 · 显示最近数据"
                : snapshot
                  ? snapshot.metadata.sessionStatus
                  : "等待行情源"
            }}
          </div>
          <button
            class="icon-button"
            title="刷新行情"
            aria-label="刷新行情"
            :disabled="loading"
            @click="loadMarket"
          >
            <RefreshCw :size="16" :class="{ spinning: loading }" /></button
          ><span class="avatar">QS</span>
        </div>
      </header>
      <div class="content">
        <div v-if="error" class="notice-bar">
          <CircleHelp :size="15" /><span>{{ error }}</span
          ><button aria-label="关闭提示" @click="error = ''">×</button>
        </div>
        <MarketView
          v-if="activeView === 'market'"
          :symbol="symbol"
          :timeframe="timeframe"
          :timeframe-options="timeframeOptions"
          :snapshot="snapshot"
          :loading="loading"
          :stale="marketStale"
          :freshness="freshness"
          :exchange="selectedExchange"
          :day-change-pct="dayChangePct"
          :timeframe-label="timeframeLabel"
          :strategies="strategies"
          :active-strategy="activeStrategy"
          :active-template="activeTemplate"
          :strategy-saved="Boolean(strategies.find((item) => item.id === activeStrategy.id))"
          :strategy-changed="strategyChanged"
          :strategy-saving="strategySaving"
          @update:symbol="symbol = $event"
          @update:timeframe="timeframe = $event"
          @select-strategy="selectLiveStrategy"
          @save-strategy="saveStrategy"
          @refresh="loadMarket"
          @navigate-ai="activeView = 'ai'"
          @navigate-backtest="activeView = 'backtest'"
        />
        <StrategyView
          v-else-if="activeView === 'strategy'"
          :strategies="strategies"
          :templates="strategyTemplates"
          :strategy-loading="strategiesLoading"
          :active-strategy="activeStrategy"
          :active-template="activeTemplate"
          :strategy-type-label="strategyTypeLabel"
          :saving="strategySaving"
          :changed="strategyChanged"
          @new-strategy="startNewStrategy"
          @select-strategy="selectStrategy"
          @select-type="selectStrategyType"
          @save="saveStrategy"
          @reload="loadStrategyWorkspace"
          @navigate-backtest="activeView = 'backtest'"
        />
        <BacktestView
          v-else-if="activeView === 'backtest'"
          :strategy-name="activeStrategy.name"
          :strategy-type="activeStrategy.strategyType"
          :symbol="symbol"
          :symbols="mlSymbols"
          :timeframe-label="timeframeLabel"
          :loading="loading"
          :form="backtestForm"
          :result="backtestResult"
          :source-time="snapshot?.metadata.sourceTime"
          @update:symbols="mlSymbols = $event"
          @run="runBacktest"
          @navigate-strategy="activeView = 'strategy'"
        />
        <AIView
          v-else
          :settings="aiSettings"
          :busy="aiBusy"
          :message="aiMessage"
          :snapshot="snapshot"
          :result="aiResult"
          @save="saveAISettings"
          @test="testAI"
          @analyze="askAI"
        />
      </div>
      <footer class="app-footer">
        <span><span class="state-dot"></span> 本地研究环境</span
        ><span>市场数据仅用于研究 · 不构成投资建议</span
        ><span>Quant Studio 0.1.0</span>
      </footer>
    </section>
  </main>
</template>
