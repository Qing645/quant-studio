<script setup lang="ts">
import { Bell, ChevronRight, X } from "@lucide/vue";
import type { SignalHistoryRecord } from "../types";

const props = defineProps<{
  records: SignalHistoryRecord[];
  toast: SignalHistoryRecord | null;
  open: boolean;
}>();

const emit = defineEmits<{
  toggle: [];
  openHistory: [];
  dismissToast: [];
}>();

/** Format stored exchange timestamps consistently for the user's market timezone. */
function displayTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(date);
}
</script>

<template>
  <button
    class="signal-center-trigger"
    :aria-expanded="open"
    aria-controls="signal-history-panel"
    aria-label="信号记录"
    title="信号记录"
    @click="emit('toggle')"
  >
    <Bell :size="16" />
    <span v-if="records.length" class="signal-center-count">{{ records.length > 99 ? "99+" : records.length }}</span>
  </button>

  <Transition name="signal-popover">
    <section
      v-if="open"
      id="signal-history-panel"
      class="signal-history-panel"
      aria-label="所有信号记录"
      @click.stop
    >
      <header class="signal-history-header">
        <div><b>信号记录</b><span>{{ records.length }} 条</span></div>
        <button class="signal-icon-action" aria-label="关闭信号记录" title="关闭" @click="emit('toggle')"><X :size="16" /></button>
      </header>
      <div v-if="records.length" class="signal-history-list">
        <article v-for="record in records" :key="record.id" class="signal-history-item">
          <div class="signal-history-meta">
            <b>{{ record.symbol }}</b>
            <span>{{ record.timeframeLabel }} · {{ displayTime(record.sourceTime || record.capturedAt) }}</span>
          </div>
          <strong>{{ record.signal }}</strong>
          <div class="signal-history-strategy">{{ record.strategyName }}</div>
          <p>{{ record.message }}</p>
        </article>
      </div>
      <div v-else class="signal-history-empty">暂无信号变化记录</div>
    </section>
  </Transition>

  <Transition name="signal-toast">
    <section
      v-if="toast"
      class="signal-toast"
      :class="{ 'signal-toast-below-panel': open }"
      aria-live="polite"
      aria-label="新信号通知"
    >
      <header><span>策略信号变化</span><button class="signal-icon-action" aria-label="关闭通知" title="关闭通知" @click="emit('dismissToast')"><X :size="15" /></button></header>
      <b>{{ toast.symbol }} · {{ toast.signal }}</b>
      <span class="signal-toast-strategy">{{ toast.strategyName }} · {{ displayTime(toast.sourceTime || toast.capturedAt) }}</span>
      <p>{{ toast.message }}</p>
      <button class="signal-toast-link" @click="emit('openHistory'); emit('dismissToast')">查看记录 <ChevronRight :size="14" /></button>
    </section>
  </Transition>
</template>
