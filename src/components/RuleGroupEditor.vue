<script setup lang="ts">
import { Plus, Trash2 } from "@lucide/vue";
import RuleOperandEditor from "./RuleOperandEditor.vue";
import { createCondition } from "../services/strategy";
import type { RuleGroup, RuleOptions } from "../types";

defineProps<{ title: string; group: RuleGroup; options: RuleOptions }>();
</script>

<template>
  <section class="condition-group">
    <div class="group-heading"><h3>{{ title }}</h3>
      <label class="match-control"><span>组合方式</span><select v-model="group.match"><option value="all">全部满足（且）</option><option value="any">任一满足（或）</option></select></label>
    </div>
    <p v-if="!group.conditions.length" class="empty-rules">尚未设置{{ title }}。点击“添加条件”，定义自己的交易规则。</p>
    <div v-for="(condition, index) in group.conditions" :key="index" class="condition-card">
      <div class="condition-title"><b>条件 {{ index + 1 }}</b><button type="button" class="subtle-button" :aria-label="`删除${title}第${index + 1}条`" @click="group.conditions.splice(index, 1)"><Trash2 :size="13" /> 移除</button></div>
      <div class="comparison-row">
        <RuleOperandEditor :operand="condition.left" :options="options" label="左侧数据" />
        <label class="field operator"><span>比较关系</span><select v-model="condition.operator"><option v-for="item in options.operators" :key="item.value" :value="item.value">{{ item.label }}</option></select></label>
        <RuleOperandEditor :operand="condition.right" :options="options" label="右侧数据" />
      </div>
    </div>
    <button type="button" class="subtle-button add-condition" :disabled="group.conditions.length >= options.maxConditions" @click="group.conditions.push(createCondition())"><Plus :size="14" /> 添加条件 <span>{{ group.conditions.length }}/{{ options.maxConditions }}</span></button>
  </section>
</template>

<style scoped>
.condition-group { margin-top: 22px; border-top: 1px solid var(--line); padding-top: 18px; }
.group-heading, .condition-title { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
h3 { font-size: 14px; margin: 0; }
.match-control { display: flex; align-items: center; gap: 10px; color: var(--muted); font-size: 12px; }
select { padding: 8px; border: 1px solid var(--line); border-radius: 4px; background: var(--bg); color: inherit; font: inherit; min-width: 0; }
.empty-rules { color: var(--muted); font-size: 13px; padding: 12px 0; line-height: 1.7; }
.condition-card { border: 1px solid var(--line); border-radius: 6px; padding: 14px; margin-top: 12px; background: var(--bg); }
.condition-title { margin-bottom: 14px; font-size: 12px; color: var(--soft); }
.comparison-row { display: grid; grid-template-columns: minmax(0, 1fr) 130px minmax(0, 1fr); gap: 14px; align-items: start; }
.operator { font-size: 12px; }
.add-condition { margin-top: 12px; }
.add-condition span { color: var(--muted); }
@media (max-width: 1000px) { .comparison-row { grid-template-columns: 1fr; } .group-heading { align-items: flex-start; flex-direction: column; } }
</style>
