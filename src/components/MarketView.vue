<script setup lang="ts">
import { computed } from "vue";
import { Activity, Bot, ChartCandlestick, Database, LoaderCircle, RefreshCw, Sparkles, Wifi } from "@lucide/vue";
import MarketChart from "./MarketChart.vue";
import type { MarketSnapshot, Strategy, StrategyTemplate, Timeframe, TimeframeOption } from "../types";

const props = defineProps<{
  symbol: string;
  timeframe: Timeframe;
  timeframeOptions: TimeframeOption[];
  snapshot: MarketSnapshot | null;
  loading: boolean;
  stale: boolean;
  freshness: string;
  exchange: string;
  dayChangePct: number | null;
  timeframeLabel: string;
  strategies: Strategy[];
  activeStrategy: Strategy;
  activeTemplate: StrategyTemplate | undefined;
  strategySaved: boolean;
  strategyChanged: boolean;
  strategySaving: boolean;
}>();
const emit = defineEmits<{
  "update:symbol": [value: string];
  "update:timeframe": [value: Timeframe];
  selectStrategy: [id: string];
  saveStrategy: [];
  refresh: [];
  "navigate-ai": [];
  "navigate-backtest": [];
}>();
const isLineTimeframe = computed(() => props.timeframe === "intraday" || props.timeframe === "five_day");

/** Forward code edits to the shared symbol state in the application shell. */
function updateSymbol(event: Event): void {
  emit("update:symbol", (event.target as HTMLInputElement).value);
}

/** Route saved strategy selection to the shared application state. */
function updateStrategy(event: Event): void {
  emit("selectStrategy", (event.target as HTMLSelectElement).value);
}
</script>

<template>
  <div class="page-heading">
    <div><div class="eyebrow">MARKET MONITOR <span class="eyebrow-line"></span> {{ freshness }}</div><h1>实时策略分析</h1><p>行情、趋势结构与策略条件在同一视图追踪。</p></div>
    <div class="heading-controls">
      <label class="symbol-input"><span>标的代码</span><input :value="symbol" maxlength="11" placeholder="159977" aria-label="输入六位股票或 ETF 代码" @input="updateSymbol" @keydown.enter="emit('refresh')" /><span class="exchange">{{ exchange }}</span></label>
      <label class="strategy-select"><span>实时策略</span><select :value="activeStrategy.id || '__draft__'" :disabled="strategySaving" @change="updateStrategy"><option v-if="!activeStrategy.id" value="__draft__">{{ activeStrategy.name || '当前草稿' }}</option><option v-for="strategy in strategies" :key="strategy.id" :value="strategy.id">{{ strategy.name }}</option></select></label>
      <button v-if="strategyChanged" class="subtle-button save-live-strategy" :disabled="strategySaving" @click="emit('saveStrategy')">{{ strategySaving ? '保存中…' : strategySaved ? '保存策略修改' : '保存当前策略' }}</button>
      <div class="segmented" role="group" aria-label="行情周期"><button v-for="item in timeframeOptions" :key="item.value" :class="{ selected: timeframe === item.value }" :aria-pressed="timeframe === item.value" @click="emit('update:timeframe', item.value)">{{ item.label }}</button></div>
      <button class="primary-button" :disabled="loading" @click="emit('refresh')"><RefreshCw :size="15" /> 查询行情</button>
    </div>
  </div>

  <div v-if="snapshot" class="quote-strip">
    <div class="instrument"><span class="instrument-code">{{ snapshot.symbol }}<i>.{{ snapshot.metadata.exchange }}</i></span><span class="instrument-name">A股 / ETF · 公共行情</span></div>
    <div class="quote-price"><strong :class="dayChangePct === null ? '' : dayChangePct >= 0 ? 'up' : 'down'">{{ snapshot.analysis.price.toFixed(3) }}</strong><span :class="dayChangePct === null ? '' : dayChangePct >= 0 ? 'up' : 'down'">{{ dayChangePct === null ? "—" : `${dayChangePct >= 0 ? '+' : ''}${dayChangePct.toFixed(2)}%` }}</span></div>
    <div class="quote-meta"><span>{{ stale || !snapshot.metadata.marketOpen ? "最近有效行情" : "数据时间" }}</span><b>{{ snapshot.metadata.sourceTime }}</b></div>
    <div class="quote-meta"><span>数据来源</span><b><Wifi :size="13" /> {{ snapshot.metadata.source }}</b></div><div class="quote-meta"><span>自动刷新</span><b>60 秒</b></div>
  </div>
  <div v-else class="quote-strip quote-empty"><LoaderCircle :size="17" class="spinning" /> {{ loading ? "正在加载公开行情" : "行情暂不可用" }}</div>

  <section class="market-layout">
    <div class="chart-section">
      <div class="section-head"><div><h2>价格结构</h2><span>{{ timeframe === 'intraday' ? "当日分时价格" : isLineTimeframe ? "分钟走势 · SMA20 · SMA60" : "OHLC · SMA20 · SMA60" }}</span></div><div class="legend-inline"><i class="legend-up"></i>上涨 <i class="legend-down"></i>下跌 <span>{{ timeframeLabel }} · {{ isLineTimeframe ? "分钟走势" : "复权K线" }}</span></div></div>
      <MarketChart v-if="snapshot" :bars="snapshot.bars" :timeframe="timeframe" />
      <div v-else class="market-chart"></div>
      <div class="chart-caption"><Database :size="13" /> {{ snapshot ? `${snapshot.metadata.sessionStatus} · ${snapshot.metadata.delayNotice}` : "等待数据" }}</div>
    </div>
    <aside class="analysis-panel">
      <div class="section-head"><div><h2>策略判断</h2><span>{{ activeStrategy.name || activeTemplate?.label || '未选择策略' }} · {{ snapshot?.strategy.asOf ? `信号截至 ${snapshot.strategy.asOf.slice(0, 10)}` : snapshot?.metadata.analysisBasis ?? '等待数据' }}<em v-if="snapshot?.strategy.barStatus === 'in_progress'"> · 盘中待收盘确认</em><em v-else-if="snapshot?.strategy.barStatus === 'completed'"> · 收盘确认</em></span></div><span class="signal-tag" :class="snapshot?.strategy.entryCondition ? 'signal-positive' : ''">{{ snapshot?.strategy.signal ?? "等待数据" }}</span></div>
      <div v-if="snapshot?.strategy.supported" class="condition-list">
        <div class="condition-row"><span class="condition-icon" :class="{ pass: snapshot.strategy.entryCondition }"><Activity :size="15" /></span><span>{{ activeTemplate?.entryLabel ?? '入场条件' }} <small>{{ activeTemplate?.entryRule.replaceAll('{fastPeriod}', String(activeStrategy.fastPeriod)).replaceAll('{slowPeriod}', String(activeStrategy.slowPeriod)) }}</small></span><b :class="snapshot.strategy.entryCondition ? 'up' : 'muted'">{{ snapshot.strategy.entryCondition ? '触发' : '未触发' }}</b></div>
        <div class="condition-row"><span class="condition-icon" :class="{ pass: snapshot.strategy.exitCondition }"><ChartCandlestick :size="15" /></span><span>{{ activeTemplate?.exitLabel ?? '退出条件' }} <small>{{ activeTemplate?.exitRule.replaceAll('{fastPeriod}', String(activeStrategy.fastPeriod)).replaceAll('{slowPeriod}', String(activeStrategy.slowPeriod)) }}</small></span><b :class="snapshot.strategy.exitCondition ? 'down' : 'muted'">{{ snapshot.strategy.exitCondition ? '触发' : '未触发' }}</b></div>
      </div>
      <div v-if="snapshot && !snapshot.strategy.supported" class="assessment"><div class="assessment-title"><Database :size="14" /> 当前周期不适用</div><p>{{ snapshot.strategy.message }}</p></div>
      <div v-else-if="snapshot?.strategy.message" class="assessment"><div class="assessment-title"><Sparkles :size="14" /> 信号说明</div><p>{{ snapshot.strategy.message }}</p></div>
      <div v-if="snapshot" class="metric-grid"><div><span>SMA20</span><b>{{ snapshot.analysis.sma20.toFixed(3) }}</b></div><div><span>SMA60</span><b>{{ snapshot.analysis.sma60.toFixed(3) }}</b></div><div><span>RSI14</span><b :class="snapshot.analysis.rsi14 < 30 ? 'amber' : ''">{{ snapshot.analysis.rsi14.toFixed(1) }}</b></div><div><span>ATR20</span><b>{{ snapshot.analysis.atr20.toFixed(3) }}</b></div></div>
      <div v-if="snapshot?.strategy.regime" class="assessment"><div class="assessment-title"><Activity :size="14" /> 市场状态</div><p>{{ snapshot.strategy.regime === 'bull' ? '牛市趋势：允许按策略持有或分批加仓。' : snapshot.strategy.regime === 'rebound_watch' ? '反弹观察：价格出现修复，但尚未完成大周期趋势确认。' : snapshot.strategy.regime === 'bear' ? '熊市趋势：优先控制仓位，避免把单日反弹当作反转。' : '趋势状态暂不可用。' }}</p></div>
      <div v-if="snapshot" class="assessment"><div class="assessment-title"><Sparkles :size="14" /> 信号依据</div><p>{{ snapshot.analysis.assessment }}</p></div>
      <button class="text-action" @click="emit('navigate-ai')"><Bot :size="15" /> 让 AI 解读当前数据 <span>→</span></button>
    </aside>
  </section>

  <section class="data-table-section">
    <div class="section-head"><div><h2>最近行情</h2><span>{{ snapshot?.bars.length ?? 0 }} 根 {{ timeframeLabel }} 数据</span></div><button class="subtle-button" @click="emit('navigate-backtest')">进入回测研究 <span>→</span></button></div>
    <div class="table-wrap"><table><thead><tr><th>时间</th><template v-if="isLineTimeframe"><th>价格</th></template><template v-else><th>开盘</th><th>最高</th><th>最低</th><th>收盘</th></template><th class="align-right">成交量</th></tr></thead><tbody><tr v-for="bar in snapshot?.bars.slice(-12).reverse() ?? []" :key="bar.datetime"><td>{{ bar.datetime }}</td><template v-if="isLineTimeframe"><td>{{ bar.close.toFixed(3) }}</td></template><template v-else><td>{{ bar.open.toFixed(3) }}</td><td class="up">{{ bar.high.toFixed(3) }}</td><td class="down">{{ bar.low.toFixed(3) }}</td><td>{{ bar.close.toFixed(3) }}</td></template><td class="align-right">{{ Math.round(bar.volume).toLocaleString() }}</td></tr></tbody></table></div>
  </section>
</template>
