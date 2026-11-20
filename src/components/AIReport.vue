<script setup lang="ts">
import { Bot } from "@lucide/vue";
import type { AIReport } from "../types";

defineProps<{ report: AIReport; stale: boolean }>();

/** Allow citation navigation only to normal web URLs, including server-validated feeds. */
function citationUrl(url: string): string | undefined {
  try {
    const parsed = new URL(url);
    return ["https:", "http:"].includes(parsed.protocol) && !parsed.username && !parsed.password ? parsed.href : undefined;
  } catch {
    return undefined;
  }
}
</script>

<template>
  <article class="ai-report">
    <div class="assessment-title"><Bot :size="14" /> 美股 · A股 · 当前策略综合分析</div>
    <div class="ai-report-meta">
      <span>生成于 {{ report.generatedAt }}</span>
      <span>市场资料采集于 {{ report.marketContextAsOf }}（各源行情时间见来源）</span>
      <span>当前标的数据截至 {{ report.targetSourceTime }}</span>
    </div>
    <p v-if="stale" class="live-ai-stale">行情已更新，以下报告基于上一时点；重新分析可获取最新判断。</p>
    <p v-if="report.completionStatus === 'truncated'" class="ai-report-incomplete" role="status">本次报告因输出上限未完成，以下仅为已返回的部分内容，未覆盖的章节尚无结论。请重新生成完整报告。</p>
    <details v-if="report.warnings.length" class="ai-report-coverage">
      <summary>数据范围与缺失项（{{ report.warnings.length }}）</summary>
      <ul><li v-for="warning in report.warnings" :key="warning">{{ warning }}</li></ul>
    </details>
    <p class="ai-report-body">{{ report.analysis }}</p>
    <details class="ai-report-sources">
      <summary>数据与事件来源（{{ report.sources.length }}）</summary>
      <ol>
        <li v-for="source in report.sources" :key="source.id">
          <a :href="citationUrl(source.url)" target="_blank" rel="noopener noreferrer">[{{ source.id }}] {{ source.label }}</a>
          <time>{{ source.asOf }}</time>
        </li>
      </ol>
    </details>
  </article>
</template>

<style scoped>
.ai-report{margin-top:13px;padding:16px 18px;border:1px solid #2a3933;border-left:3px solid #4a9b79;border-radius:4px;background:#101816;overflow-wrap:anywhere}
.ai-report-meta{display:grid;gap:4px;margin-top:10px;color:#879c90;font-size:10px;line-height:1.6}
.ai-report-body{max-height:680px;overflow:auto;margin:14px 0 0;color:#d0dbd5;font-size:13px;line-height:1.95;white-space:pre-wrap}
.ai-report-coverage,.ai-report-sources{margin-top:12px;color:#9caf9f;font-size:11px;line-height:1.8}
.ai-report-coverage{color:#c7b380}
summary{cursor:pointer}
ul,ol{margin:8px 0 0;padding-left:20px}
li{margin-bottom:7px}
a{color:#8bd3b1;text-decoration:none}
a:hover{text-decoration:underline}
time{display:block;color:#819188;font-size:10px}
.live-ai-stale{color:#d9bb79}
.ai-report-incomplete{margin:12px 0 0;padding:10px;border:1px solid #665332;border-radius:4px;background:#282115;color:#e1c280;font-size:12px;line-height:1.8}
@media(max-width:480px){.ai-report{padding:12px}.ai-report-body{font-size:12px;max-height:560px}}
</style>
