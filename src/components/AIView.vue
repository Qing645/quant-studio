<script setup lang="ts">
import { Bot, LoaderCircle, Save, ShieldCheck, Sparkles } from "@lucide/vue";
import type { AISettings, MarketSnapshot } from "../types";

defineProps<{
  settings: AISettings;
  busy: boolean;
  message: string;
  snapshot: MarketSnapshot | null;
  result: string;
}>();
const emit = defineEmits<{ save: []; test: []; analyze: [] }>();
</script>

<template>
  <div class="page-heading"><div><div class="eyebrow">MODEL CONNECTION <span class="eyebrow-line"></span> OPENAI-COMPATIBLE</div><h1>AI 模型</h1><p>配置模型连接，用于解释行情信号和回测结果。</p></div><span class="secure-badge"><ShieldCheck :size="15" /> API Key 安全存储</span></div>
  <section class="ai-layout"><div class="ai-config-panel"><div class="section-head"><div><h2>服务连接</h2><span>兼容 OpenAI Chat Completions 的接口</span></div><span class="connection-state" :class="{ ready: settings.hasApiKey }"><span class="state-dot"></span>{{ settings.hasApiKey ? "已配置密钥" : "未配置密钥" }}</span></div><div class="form-grid"><label class="field full"><span>API Base URL</span><input v-model="settings.baseUrl" placeholder="https://api.openai.com/v1" /></label><label class="field full"><span>模型名称</span><input v-model="settings.model" placeholder="gpt-4o-mini" /></label><label class="field full"><span>API Key <small>新密钥保存后不会再次显示</small></span><input v-model="settings.apiKey" type="password" autocomplete="new-password" :placeholder="settings.hasApiKey ? '已保存到系统钥匙串，留空则保持不变' : '输入服务商 API Key'" /></label></div><div class="ai-actions"><button class="primary-button" @click="emit('save')"><Save :size="15" /> 保存配置</button><button class="subtle-button" :disabled="busy" @click="emit('test')">{{ busy ? "测试中…" : "测试连接" }}</button><span class="inline-message">{{ message }}</span></div><div class="privacy-note"><ShieldCheck :size="15" /> Key 由本地后端写入 macOS 钥匙串；行情分析请求只发送指标摘要，不发送账户或交易凭证。</div></div>
    <aside class="ai-analysis-panel"><div class="section-head"><div><h2>当前行情解读</h2><span>{{ snapshot ? `${snapshot.symbol} · ${snapshot.metadata.sourceTime}` : "尚无可分析行情" }}</span></div><Sparkles :size="17" class="accent-icon" /></div><p class="ai-summary">AI 将收到当前指标、策略条件、标的周期和行情时间，并只基于这些数据解释趋势、动能和风险边界。</p><button class="primary-button wide-button" :disabled="busy || !snapshot" @click="emit('analyze')"><LoaderCircle v-if="busy" :size="15" class="spinning" /><Sparkles v-else :size="15" /> 生成数据分析</button><div v-if="result" class="ai-result"><div class="assessment-title"><Bot :size="14" /> 模型分析</div><p>{{ result }}</p></div><div v-else class="ai-result-placeholder">分析结果会显示在这里，并保留本次使用的数据时间。</div></aside>
  </section>
</template>
