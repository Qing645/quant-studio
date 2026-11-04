<script setup lang="ts">
import { Activity, Plus, Save, Settings2 } from "@lucide/vue";
import type { Strategy, StrategyTemplate, StrategyType } from "../types";

defineProps<{
  strategies: Strategy[];
  templates: StrategyTemplate[];
  strategyLoading: boolean;
  activeStrategy: Strategy;
  activeTemplate: StrategyTemplate | undefined;
  strategyTypeLabel: string;
  saving: boolean;
  changed: boolean;
}>();

const emit = defineEmits<{
  newStrategy: [];
  selectStrategy: [strategy: Strategy];
  selectType: [strategyType: StrategyType];
  save: [];
  reload: [];
  navigateBacktest: [];
}>();

/** Fill the visible rule preview with the current editable periods. */
function formatRule(rule: string | undefined, strategy: Strategy): string {
  return (rule ?? "").replaceAll("{fastPeriod}", String(strategy.fastPeriod)).replaceAll("{slowPeriod}", String(strategy.slowPeriod));
}

/** Send a method selection through the parent so defaults and scope stay aligned. */
function selectMethod(event: Event): void {
  emit("selectType", (event.target as HTMLSelectElement).value as StrategyType);
}
</script>

<template>
  <div class="page-heading">
    <div>
      <div class="eyebrow">STRATEGY LAB <span class="eyebrow-line"></span> RESEARCH WORKSPACE</div>
      <h1>策略研究</h1>
      <p>在当前策略上调整方法和参数，保存后用于回测。</p>
    </div>
    <button class="primary-button" :disabled="saving || !templates.length" @click="emit('newStrategy')">
      <Plus :size="15" /> 新建策略
    </button>
  </div>
  <div class="research-layout">
    <aside class="strategy-list">
      <div class="list-title">已保存策略 <span>{{ strategies.length }}</span></div>
      <div v-if="strategyLoading" class="empty-list">正在读取已保存策略…</div>
      <button
        v-else v-for="strategy in strategies" :key="strategy.id" class="strategy-list-item"
        :class="{ chosen: activeStrategy.id === strategy.id }" :disabled="saving"
        @click="emit('selectStrategy', strategy)"
      >
        <span class="strategy-symbol"><Activity :size="15" /></span>
        <span>
          <b>{{ strategy.name }}</b>
          <small>{{ templates.find((item) => item.strategyType === strategy.strategyType)?.label ?? '' }} · v{{ strategy.version }}</small>
        </span>
      </button>
      <div v-if="!strategyLoading && !strategies.length" class="empty-list">
        <span>没有读取到已保存策略</span>
        <button class="subtle-button reload-button" :disabled="saving" @click="emit('reload')">重新加载</button>
      </div>
    </aside>
    <section class="editor-section">
      <div class="section-head">
        <div><h2>当前策略</h2><span>切换计算方法后，保存将更新当前记录</span></div>
        <span class="draft-tag" role="status">
          {{ saving ? '保存中…' : !activeStrategy.id ? '未保存草稿' : changed ? '有未保存修改' : `已保存 · v${activeStrategy.version}` }}
        </span>
      </div>
      <fieldset class="strategy-fields" :disabled="saving || !activeTemplate">
        <legend class="sr-only">策略设置</legend>
        <div class="form-grid">
          <label class="field full">
            <span>策略名称</span><input v-model="activeStrategy.name" maxlength="60" />
          </label>
          <label class="field full">
            <span>计算方法</span>
            <select :value="activeStrategy.strategyType" @change="selectMethod">
              <option v-for="template in templates" :key="template.strategyType" :value="template.strategyType">{{ template.label }}</option>
            </select>
          </label>
          <label v-if="activeStrategy.strategyType === 'trend_filter'" class="field">
            <span>长期趋势均线</span>
            <div class="number-field"><input v-model.number="activeStrategy.slowPeriod" type="number" min="60" max="250" step="10" /><span>根日K SMA</span></div>
          </label>
          <template v-else-if="activeStrategy.strategyType !== 'ml_direction'">
            <label class="field">
              <span>快线周期</span>
              <div class="number-field"><input v-model.number="activeStrategy.fastPeriod" type="number" min="2" max="100" /><span>根K线 SMA</span></div>
            </label>
            <label class="field">
              <span>慢线周期</span>
              <div class="number-field"><input v-model.number="activeStrategy.slowPeriod" type="number" min="3" max="250" /><span>根K线 SMA</span></div>
            </label>
          </template>
          <label class="field full"><span>策略说明</span><textarea v-model="activeStrategy.description" rows="3" maxlength="500"></textarea></label>
        </div>
      </fieldset>
      <div v-if="activeTemplate" class="rule-preview">
        <div class="preview-head"><Settings2 :size="15" /> {{ strategyTypeLabel }}规则预览 · {{ activeTemplate.scopeLabel }}</div>
        <div><span class="rule-token">{{ activeTemplate.entryLabel }}</span> {{ formatRule(activeTemplate.entryRule, activeStrategy) }}</div>
        <div><span class="rule-token exit">{{ activeTemplate.exitLabel }}</span> {{ formatRule(activeTemplate.exitRule, activeStrategy) }}</div>
        <small>{{ activeTemplate.executionNote }}</small>
      </div>
      <div class="form-actions">
        <button class="primary-button" :disabled="saving || !changed || !activeTemplate || !activeStrategy.name.trim() || activeStrategy.fastPeriod >= activeStrategy.slowPeriod" @click="emit('save')">
          <Save :size="15" /> {{ saving ? '保存中…' : !activeStrategy.id ? '保存策略' : changed ? '保存修改' : '已保存' }}
        </button>
        <button class="subtle-button" :disabled="saving || !activeTemplate" @click="emit('navigateBacktest')">回测当前配置 <span>→</span></button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.strategy-fields { min-width: 0; margin: 0; padding: 0; border: 0; }
.field select { width: 100%; min-height: 35px; padding: 9px 10px; border: 1px solid var(--line); border-radius: 4px; background: var(--bg); color: inherit; font: inherit; color-scheme: dark; }
.field select:focus-visible { outline: 2px solid var(--green); outline-offset: 2px; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
</style>
