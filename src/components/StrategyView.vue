<script setup lang="ts">
import { Activity, Plus, Save, Settings2 } from "@lucide/vue";
import { computed } from "vue";
import CustomRuleEditor from "./CustomRuleEditor.vue";
import StrategyParameters from "./StrategyParameters.vue";
import { strategyPresentation, strategyValidationError } from "../services/strategy";
import type { Strategy, StrategyTemplate, RuleOptions } from "../types";

const props = defineProps<{
  strategies: Strategy[];
  ruleOptions: RuleOptions | null;
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
  save: [];
  reload: [];
  navigateBacktest: [];
}>();

const validationError = computed(() => strategyValidationError(props.activeStrategy, props.activeTemplate, props.ruleOptions));
const presentation = computed(() => strategyPresentation(props.activeStrategy, props.activeTemplate, props.ruleOptions));

</script>

<template>
  <div class="page-heading">
    <div>
      <div class="eyebrow">STRATEGY LAB <span class="eyebrow-line"></span> RESEARCH WORKSPACE</div>
      <h1>策略研究</h1>
      <p>管理独立策略及其参数，保存后用于实时分析和回测。</p>
    </div>
    <button class="primary-button" :disabled="saving || !ruleOptions" @click="emit('newStrategy')">
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
          <small>{{ strategy.strategyType === 'custom' ? `${strategy.rules.symbol} · ${ruleOptions?.timeframes.find((item) => item.value === strategy.rules.timeframe)?.label ?? ''}` : templates.find((item) => item.strategyType === strategy.strategyType)?.label ?? '' }} · v{{ strategy.version }}</small>
        </span>
      </button>
      <div v-if="!strategyLoading && !strategies.length" class="empty-list">
        <span>没有读取到已保存策略</span>
        <button class="subtle-button reload-button" :disabled="saving" @click="emit('reload')">重新加载</button>
      </div>
    </aside>
    <section class="editor-section">
      <div class="section-head">
        <div><h2>当前策略</h2><span>每条策略独立保存规则和参数</span></div>
        <span class="draft-tag" role="status">
          {{ saving ? '保存中…' : !activeStrategy.id ? '未保存草稿' : changed ? '有未保存修改' : `已保存 · v${activeStrategy.version}` }}
        </span>
      </div>
      <fieldset class="strategy-fields" :disabled="saving || (activeStrategy.strategyType === 'custom' ? !ruleOptions : !activeTemplate)">
        <legend class="sr-only">策略设置</legend>
        <div class="form-grid">
          <label class="field full">
            <span>策略名称</span><input v-model="activeStrategy.name" maxlength="60" />
          </label>
          <div v-if="activeStrategy.strategyType !== 'custom'" class="field full"><span>内置策略规则</span><strong>{{ activeTemplate?.label }}</strong></div>
          <template v-if="activeStrategy.strategyType !== 'custom'">
          <label v-if="activeStrategy.strategyType === 'trend_filter'" class="field">
            <span>中期趋势确认均线</span>
            <div class="number-field"><input v-model.number="activeStrategy.slowPeriod" type="number" :min="activeStrategy.fastPeriod + 1" max="250" step="1" /><span>根日K SMA</span></div>
          </label>
          <template v-else-if="activeStrategy.strategyType === 'intraday_scalp' || activeStrategy.strategyType === 'swing_breakout'">
            <label class="field">
              <span>{{ activeStrategy.strategyType === 'intraday_scalp' ? '快线周期' : '退出均线周期' }}</span>
              <div class="number-field"><input v-model.number="activeStrategy.fastPeriod" type="number" min="2" max="100" /><span>{{ activeStrategy.strategyType === 'intraday_scalp' ? '根分钟K EMA' : '根日K SMA' }}</span></div>
            </label>
            <label class="field">
              <span>{{ activeStrategy.strategyType === 'intraday_scalp' ? '慢线周期' : '突破回看周期' }}</span>
              <div class="number-field"><input v-model.number="activeStrategy.slowPeriod" type="number" min="3" max="250" /><span>{{ activeStrategy.strategyType === 'intraday_scalp' ? '根分钟K EMA' : '根日K高点' }}</span></div>
            </label>
          </template>
          <StrategyParameters v-if="activeTemplate" :strategy="activeStrategy" :template="activeTemplate" />
          </template>
          <label class="field full"><span>策略说明</span><textarea v-model="activeStrategy.description" rows="3" maxlength="500"></textarea><small>用于记录研究目的；实际计算以参数和下方规则预览为准。</small></label>
        </div>
        <CustomRuleEditor v-if="activeStrategy.strategyType === 'custom' && ruleOptions" :key="activeStrategy.id || 'new'" :rules="activeStrategy.rules" :options="ruleOptions" />
      </fieldset>
      <div v-if="presentation" class="rule-preview">
        <div class="preview-head"><Settings2 :size="15" /> {{ strategyTypeLabel }}规则预览 · {{ presentation.scopeLabel }}</div>
        <div><span class="rule-token">{{ presentation.entryLabel }}</span> {{ presentation.entryRule }}</div>
        <div><span class="rule-token exit">{{ presentation.exitLabel }}</span> {{ presentation.exitRule }}</div>
        <small>{{ presentation.executionNote }}</small>
      </div>
      <p v-if="validationError" class="validation-error" role="alert">{{ validationError }}</p>
      <div class="form-actions">
        <button class="primary-button" :disabled="saving || !changed || !presentation || !activeStrategy.name.trim() || Boolean(validationError)" @click="emit('save')">
          <Save :size="15" /> {{ saving ? '保存中…' : !activeStrategy.id ? '保存策略' : changed ? '保存修改' : '已保存' }}
        </button>
        <button class="subtle-button" :disabled="saving || !presentation || Boolean(validationError)" @click="emit('navigateBacktest')">回测当前配置 <span>→</span></button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.strategy-fields { min-width: 0; margin: 0; padding: 0; border: 0; }
.validation-error { color: var(--red); font-size: 13px; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
</style>
