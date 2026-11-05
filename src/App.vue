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
import SignalCenter from "./components/SignalCenter.vue";
import StrategyView from "./components/StrategyView.vue";
import { api } from "./services/api";
import { copyStrategy, createCustomStrategy, marketStrategyQuery, strategyExecution, strategyPresentation, strategyValidationError } from "./services/strategy";
import type {
  AISettings,
  BacktestResult,
  MarketSnapshot,
  Strategy,
  SignalHistoryRecord,
  StrategyTemplate,
  RuleOptions,
  Timeframe,
  TimeframeOption,
  View
} from "./types";

const activeView = ref<View>("market");
const timeframe = ref<Timeframe>("daily");
const symbol = ref("159977");
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
const signalHistory = ref<SignalHistoryRecord[]>(readSignalHistory());
const signalHistoryOpen = ref(false);
const activeSignalToast = ref<SignalHistoryRecord | null>(null);
const strategies = ref<Strategy[]>([]);
const strategyTemplates = ref<StrategyTemplate[]>([]);
const ruleOptions = ref<RuleOptions | null>(null);
const strategiesLoading = ref(false);
const strategySaving = ref(false);
const loading = ref(false);
const apiReady = ref(false);
const marketStale = ref(false);
const error = ref("");
const activeStrategy = ref<Strategy>({ ...createCustomStrategy("159977", "daily"), name: "" });
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
const liveAIResult = ref("");
const liveAIResultSourceTime = ref("");
const liveAIError = ref("");
const liveAIBusy = ref(false);
let liveAIRequestId = 0;
let refreshTimer: number | undefined;
let refreshQueued = false;
let marketRequestId = 0;
let observedSignalContext = "";
let observedSignalFingerprint = "";
let signalToastTimer: number | undefined;

const signalContext = computed(() => JSON.stringify([
  symbol.value.trim().toUpperCase().replace(/\.(SZSE|SSE|SZ|SH)$/, ""),
  timeframe.value, activeStrategy.value.id, strategyExecution(activeStrategy.value)
]));

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
    (item) => item.strategyType === activeStrategy.value.strategyType
  )
);
const activePresentation = computed(() => strategyPresentation(activeStrategy.value, activeTemplate.value, ruleOptions.value));
const strategyTypeLabel = computed(() => activePresentation.value?.label ?? "");
const backtestRequest = computed(() => ({
  symbol: activeStrategy.value.strategyType === "custom" ? activeStrategy.value.rules.symbol : symbol.value,
  timeframe: activeStrategy.value.strategyType === "custom" ? activeStrategy.value.rules.timeframe
    : activeStrategy.value.strategyType === "intraday_scalp" ? "intraday" : "daily",
  ...strategyExecution(activeStrategy.value), ...backtestForm
}));
const backtestTimeframeLabel = computed(() => timeframeOptions.find((item) => item.value === backtestRequest.value.timeframe)?.label ?? "");
const backtestContext = computed(() => JSON.stringify(backtestRequest.value));
const strategyChanged = computed(() => {
  const saved = strategies.value.find((item) => item.id === activeStrategy.value.id);
  return (
    !saved || strategyDefinition(saved) !== strategyDefinition(activeStrategy.value)
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
const liveAIContext = computed(() => JSON.stringify([
  symbol.value.trim().toUpperCase(), timeframe.value, strategyExecution(activeStrategy.value)
]));
const liveAIStale = computed(() => Boolean(
  liveAIResult.value &&
  snapshot.value &&
  liveAIResultSourceTime.value !== snapshot.value.metadata.sourceTime
));

/** Load the selected symbol and queue one refresh if a request is already running. */
async function loadMarket(): Promise<void> {
  const validationError = strategyValidationError(activeStrategy.value, activeTemplate.value, ruleOptions.value);
  if (validationError) {
    if (activeView.value === "market" && activeStrategy.value.name) error.value = `当前策略尚未就绪：${validationError}`;
    return;
  }
  if (loading.value) {
    refreshQueued = true;
    return;
  }
  loading.value = true;
  const requestId = ++marketRequestId;
  const requestedTimeframe = timeframe.value;
  error.value = "";
  const strategyQuery = marketStrategyQuery(activeStrategy.value);
  const requestedSymbol = symbol.value
    .trim()
    .toUpperCase()
    .replace(/\.(SZSE|SSE|SZ|SH)$/, "");
  const requestedSignalContext = signalContext.value;
  const requestedStrategy = copyStrategy(activeStrategy.value);
  if (snapshot.value && snapshot.value.symbol !== requestedSymbol) {
    snapshot.value = null;
    aiResult.value = "";
    backtestResult.value = null;
  }
  try {
    const result = await api<MarketSnapshot>(
      `/api/market?symbol=${encodeURIComponent(symbol.value)}&timeframe=${requestedTimeframe}&${strategyQuery}`
    );
    if (
      requestId === marketRequestId &&
      requestedTimeframe === timeframe.value &&
      requestedSignalContext === signalContext.value
    ) {
      recordSignalTransition(result, requestedSignalContext, requestedStrategy);
      snapshot.value = result;
      marketStale.value = false;
      apiReady.value = true;
    }
  } catch (cause) {
    if (requestId === marketRequestId) {
      error.value = cause instanceof Error ? cause.message : "行情读取失败";
      marketStale.value = Boolean(snapshot.value);
      apiReady.value = true;
    }
  } finally {
    loading.value = false;
    if (refreshQueued) {
      refreshQueued = false;
      void loadMarket();
    }
  }
}

/** Load persisted signal events without failing when browser storage is unavailable. */
function readSignalHistory(): SignalHistoryRecord[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem("quant-studio.signal-history.v1") ?? "[]");
    return Array.isArray(value) ? value as SignalHistoryRecord[] : [];
  } catch {
    return [];
  }
}

/** Store the latest signal history for access after the workspace is reopened. */
function persistSignalHistory(records: SignalHistoryRecord[]): void {
  try {
    localStorage.setItem("quant-studio.signal-history.v1", JSON.stringify(records));
  } catch {
    /* Keep this session's records available even if browser storage is full. */
  }
}

/** Record only signal-state transitions within the same symbol, timeframe and strategy. */
function recordSignalTransition(
  result: MarketSnapshot,
  context: string,
  strategy: Strategy
): void {
  const current = result.strategy;
  if (!current.supported || !current.signal || current.signal === "数据不足") {
    observedSignalContext = context;
    observedSignalFingerprint = "";
    return;
  }

  const fingerprint = JSON.stringify([
    current.signal,
    current.entryCondition ?? false,
    current.exitCondition ?? false,
    current.action ?? "",
    current.regime ?? ""
  ]);
  if (context !== observedSignalContext || !observedSignalFingerprint) {
    observedSignalContext = context;
    observedSignalFingerprint = fingerprint;
    return;
  }
  if (fingerprint === observedSignalFingerprint) return;
  observedSignalFingerprint = fingerprint;

  const record: SignalHistoryRecord = {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    symbol: result.symbol,
    strategyName: strategy.name || strategy.strategyType,
    strategyType: strategy.strategyType,
    timeframeLabel: timeframeOptions.find((item) => item.value === result.timeframe)?.label ?? result.timeframe,
    signal: current.signal,
    message: current.message,
    sourceTime: result.metadata.sourceTime,
    capturedAt: new Date().toISOString()
  };
  signalHistory.value = [record, ...signalHistory.value];
  persistSignalHistory(signalHistory.value);
  activeSignalToast.value = record;
  if (signalToastTimer) window.clearTimeout(signalToastTimer);
  signalToastTimer = window.setTimeout(() => {
    activeSignalToast.value = null;
  }, 8000);
}

/** Dismiss the current signal toast while leaving its record in history. */
function dismissSignalToast(): void {
  activeSignalToast.value = null;
  if (signalToastTimer) window.clearTimeout(signalToastTimer);
  signalToastTimer = undefined;
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

/** Load the rule vocabulary independently of the existing built-in strategy catalog. */
async function loadRuleOptions(): Promise<void> {
  try {
    ruleOptions.value = await api<RuleOptions>("/api/strategy-rule-options");
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "规则编辑器加载失败";
  }
}

/** Restore the most recent saved strategy as soon as its metadata is available. */
async function loadStrategyWorkspace(): Promise<void> {
  await Promise.all([loadStrategyTemplates(), loadStrategies(), loadRuleOptions()]);
  if (activeStrategy.value.name) return;
  if (strategies.value[0]) selectStrategy(strategies.value[0]);
  else startNewStrategy();
}

/** Compare editable fields only so metadata does not create unnecessary revisions. */
function strategyDefinition(strategy: Strategy): string {
  return JSON.stringify([strategy.name.trim(), strategyExecution(strategy), strategy.description]);
}

/** Keep the selected market aligned while leaving timeframe choice to the user. */
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
}

/** Create a blank user-authored rule set, independent of the currently selected strategy. */
function startNewStrategy(): void {
  if (strategySaving.value || !ruleOptions.value) return;
  activeStrategy.value = createCustomStrategy(symbol.value, timeframe.value);
  backtestResult.value = null;
}

/** Save once per edit, keeping the server identity and revision on the active record. */
async function saveStrategy(): Promise<void> {
  if (strategySaving.value || !strategyChanged.value)
    return;
  if (!activeStrategy.value.name.trim()) {
    error.value = "请输入策略名称";
    return;
  }
  const validationError = strategyValidationError(activeStrategy.value, activeTemplate.value, ruleOptions.value);
  if (validationError) {
    error.value = validationError;
    return;
  }
  strategySaving.value = true;
  try {
    const saved = await api<Strategy>("/api/strategies", {
      method: "POST",
      body: JSON.stringify({
        ...activeStrategy.value,
        name: activeStrategy.value.name.trim()
      })
    });
    activeStrategy.value = copyStrategy(saved);
    if (saved.strategyType === "custom") symbol.value = saved.rules.symbol;
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

/** Copy a saved strategy into the editor for review or modification. */
function selectStrategy(strategy: Strategy): void {
  if (strategySaving.value) return;
  backtestResult.value = null;
  activeStrategy.value = copyStrategy(strategy);
  const template = strategyTemplates.value.find(
    (item) => item.strategyType === strategy.strategyType
  );
  if (template) alignStrategyScope(template);
  if (strategy.strategyType === "custom") symbol.value = strategy.rules.symbol;
  void loadMarket();
}

/** Run the selected rule set with the current fee and slippage inputs. */
async function runBacktest(): Promise<void> {
  const validationError = strategyValidationError(activeStrategy.value, activeTemplate.value, ruleOptions.value);
  if (validationError) {
    error.value = validationError;
    return;
  }
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

/** Explain the displayed snapshot and discard replies for superseded strategy parameters. */
async function askAI(): Promise<void> {
  const current = snapshot.value;
  if (!current) return;
  const context = liveAIContext.value;
  aiBusy.value = true;
  aiResult.value = "";
  try {
    const result = await api<{ analysis: string }>("/api/ai/analyze", {
      method: "POST",
      body: JSON.stringify({
        symbol: current.symbol,
        timeframe: timeframe.value,
        sourceTime: current.metadata.sourceTime,
        analysisBasis: current.metadata.analysisBasis,
        analysis: current.analysis,
        strategy: current.strategy,
        bars: current.bars.slice(-500)
      })
    });
    if (context === liveAIContext.value) aiResult.value = result.analysis;
  } catch (cause) {
    if (context === liveAIContext.value)
      aiResult.value = cause instanceof Error ? cause.message : "AI 分析失败";
  } finally {
    aiBusy.value = false;
  }
}

/** Analyze the displayed live snapshot and discard replies for an older context. */
async function analyzeLiveMarket(): Promise<void> {
  const current = snapshot.value;
  if (!current || liveAIBusy.value) return;
  const requestId = ++liveAIRequestId;
  const context = liveAIContext.value;
  liveAIBusy.value = true;
  liveAIResult.value = "";
  liveAIError.value = "";
  try {
    const result = await api<{ analysis: string }>("/api/ai/analyze", {
      method: "POST",
      body: JSON.stringify({
        symbol: current.symbol,
        timeframe: timeframe.value,
        sourceTime: current.metadata.sourceTime,
        analysisBasis: current.metadata.analysisBasis,
        analysis: current.analysis,
        strategy: current.strategy,
        bars: current.bars.slice(-500)
      })
    });
    if (requestId === liveAIRequestId && context === liveAIContext.value) {
      liveAIResult.value = result.analysis;
      liveAIResultSourceTime.value = current.metadata.sourceTime;
    }
  } catch (cause) {
    if (requestId === liveAIRequestId && context === liveAIContext.value) {
      liveAIError.value = cause instanceof Error ? cause.message : "AI 分析失败";
    }
  } finally {
    if (requestId === liveAIRequestId) liveAIBusy.value = false;
  }
}

watch(liveAIContext, () => {
  aiResult.value = "";
  liveAIRequestId += 1;
  liveAIResult.value = "";
  liveAIResultSourceTime.value = "";
  liveAIError.value = "";
  liveAIBusy.value = false;
}, { flush: "sync" });

watch(
  backtestContext,
  () => {
    backtestResult.value = null;
  },
  { flush: "sync" }
);
watch(signalContext, () => {
  snapshot.value = null;
  observedSignalContext = "";
  observedSignalFingerprint = "";
  dismissSignalToast();
}, { flush: "sync" });
watch(timeframe, () => {
  marketRequestId += 1;
  snapshot.value = null;
  marketStale.value = false;
  aiResult.value = "";
  backtestResult.value = null;
  void loadMarket();
});
watch(
  () => JSON.stringify([activeStrategy.value.id, strategyExecution(activeStrategy.value)]),
  () => {
    if (activeView.value === "market") void loadMarket();
  }
);
watch(activeView, (view) => {
  if (view === "market" && !snapshot.value) void loadMarket();
  if (view === "strategy" && (!strategies.value.length || !strategyTemplates.value.length || !ruleOptions.value)) {
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
    void loadMarket();
  }, 60_000);
});
onUnmounted(() => {
  if (refreshTimer) window.clearInterval(refreshTimer);
  dismissSignalToast();
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
          >
          <SignalCenter
            :records="signalHistory"
            :toast="activeSignalToast"
            :open="signalHistoryOpen"
            @toggle="signalHistoryOpen = !signalHistoryOpen"
            @open-history="signalHistoryOpen = true"
            @dismiss-toast="dismissSignalToast"
          />
          <span class="avatar">QS</span>
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
          :active-template="activePresentation"
          :strategy-saved="Boolean(strategies.find((item) => item.id === activeStrategy.id))"
          :strategy-changed="strategyChanged"
          :strategy-saving="strategySaving"
          :ai-result="liveAIResult"
          :ai-error="liveAIError"
          :ai-busy="liveAIBusy"
          :ai-ready="aiSettings.hasApiKey"
          :ai-stale="liveAIStale"
          @update:symbol="symbol = $event"
          @update:timeframe="timeframe = $event"
          @select-strategy="selectLiveStrategy"
          @save-strategy="saveStrategy"
          @refresh="loadMarket"
          @analyze-ai="analyzeLiveMarket"
          @navigate-ai="activeView = 'ai'"
          @navigate-backtest="activeView = 'backtest'"
        />
        <StrategyView
          v-else-if="activeView === 'strategy'"
          :strategies="strategies"
          :templates="strategyTemplates"
          :rule-options="ruleOptions"
          :strategy-loading="strategiesLoading"
          :active-strategy="activeStrategy"
          :active-template="activeTemplate"
          :strategy-type-label="strategyTypeLabel"
          :saving="strategySaving"
          :changed="strategyChanged"
          @new-strategy="startNewStrategy"
          @select-strategy="selectStrategy"
          @save="saveStrategy"
          @reload="loadStrategyWorkspace"
          @navigate-backtest="activeView = 'backtest'"
        />
        <BacktestView
          v-else-if="activeView === 'backtest'"
          :strategy-name="activeStrategy.name"
          :strategy-type="activeStrategy.strategyType"
          :symbol="backtestRequest.symbol"
          :timeframe-label="backtestTimeframeLabel"
          :loading="loading"
          :form="backtestForm"
          :result="backtestResult"
          :source-time="snapshot?.metadata.sourceTime"
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
