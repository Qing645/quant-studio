<script setup lang="ts">
import { LoaderCircle, Save, ShieldCheck, Sparkles } from "@lucide/vue";
import AIReportView from "./AIReport.vue";
import type { AIReport, AISettings, MarketSnapshot } from "../types";

defineProps<{
  settings: AISettings;
  busy: boolean;
  analysisBusy: boolean;
  message: string;
  snapshot: MarketSnapshot | null;
  report: AIReport | null;
  analysisError: string;
  stale: boolean;
}>();
const emit = defineEmits<{ save: []; test: []; analyze: [] }>();
</script>

<template>
  <div class="page-heading"><div><div class="eyebrow">MODEL CONNECTION <span class="eyebrow-line"></span> OPENAI-COMPATIBLE</div><h1>AI 模型</h1><p>配置模型连接，综合美股、A股、近期事件与当前策略进行研究。</p></div><span class="secure-badge"><ShieldCheck :size="15" /> API Key 安全存储</span></div>
  <section class="ai-layout"><div class="ai-config-panel"><div class="section-head"><div><h2>服务连接</h2><span>兼容 OpenAI Chat Completions 的接口</span></div><span class="connection-state" :class="{ ready: settings.hasApiKey }"><span class="state-dot"></span>{{ settings.hasApiKey ? "已配置密钥" : "未配置密钥" }}</span></div><div class="form-grid"><label class="field"><span>API Base URL</span><input v-model="settings.baseUrl" placeholder="https://api.openai.com/v1" /></label><label class="field"><span>模型名称 <small>必填 · 服务商提供的模型 ID</small></span><input v-model.trim="settings.model" required maxlength="120" placeholder="填写模型 ID，例如 gpt-4o-mini" /></label><label class="field"><span>API Key <small>新密钥保存后不会再次显示</small></span><input v-model="settings.apiKey" type="password" autocomplete="new-password" :placeholder="settings.hasApiKey ? '已保存到系统钥匙串，留空则保持不变' : '输入服务商 API Key'" /></label></div><div class="ai-actions"><button class="primary-button" :disabled="busy || analysisBusy" @click="emit('save')"><Save :size="15" /> 保存配置</button><button class="subtle-button" :disabled="busy || analysisBusy" @click="emit('test')">{{ busy ? "测试中…" : "测试连接" }}</button><span class="inline-message">{{ message }}</span></div><div class="privacy-note"><ShieldCheck :size="15" /> Key 由本地后端写入 macOS 钥匙串；分析请求会将当前标的K线、策略配置及公开市场和新闻摘要发送给配置的模型服务，不包含账户或交易凭证。</div></div>
    <aside class="ai-analysis-panel"><div class="section-head"><div><h2>跨市场与策略综合报告</h2><span>{{ snapshot ? `${snapshot.symbol} · ${snapshot.metadata.sourceTime}` : "尚无可分析行情" }}</span></div><Sparkles :size="17" class="accent-icon" /></div><p class="ai-summary">综合美股三大指数、费半、VIX、美债与美元，结合A股指数走势、成交与资金、行业排行和近期事件，分析阶段、关键价位、仓位节奏与当前策略。缺失数据会明确列出。</p><button class="primary-button wide-button" :disabled="busy || analysisBusy || !snapshot || !settings.hasApiKey" @click="emit('analyze')"><LoaderCircle v-if="analysisBusy" :size="15" class="spinning" /><Sparkles v-else :size="15" /> {{ analysisBusy ? "正在采集资料并生成报告…" : "生成综合分析" }}</button><p v-if="analysisError" class="live-ai-error">{{ analysisError }}</p><AIReportView v-else-if="report" :report="report" :stale="stale" /><div v-else class="ai-result-placeholder">分析结果会显示在这里，并保留本次使用的数据时间。</div></aside>
  </section>
</template>
