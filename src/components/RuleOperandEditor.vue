<script setup lang="ts">
import { computed } from "vue";
import type { RuleIndicator, RuleOperand, RuleOptions } from "../types";

const props = defineProps<{ operand: RuleOperand; options: RuleOptions; label: string }>();
const indicator = computed(() => props.options.indicators.find((item) => item.value === props.operand.kind));

/** Remove fields from the previous reference kind so the saved contract stays unambiguous. */
function changeIndicator(event: Event): void {
  const kind = (event.target as HTMLSelectElement).value as RuleIndicator;
  props.operand.kind = kind;
  delete props.operand.period;
  delete props.operand.value;
  if (props.options.indicators.find((item) => item.value === kind)?.period) props.operand.period = kind === "rsi" ? 14 : 20;
  if (kind === "number") {
    props.operand.value = 0;
    props.operand.offset = 0;
  }
}
</script>

<template>
  <div class="operand-editor">
    <label class="field operand-kind"><span>{{ label }}</span>
      <select :value="operand.kind" @change="changeIndicator"><option v-for="item in options.indicators" :key="item.value" :value="item.value">{{ item.label }}</option></select>
    </label>
    <label v-if="indicator?.period" class="field"><span>指标周期</span><input v-model.number="operand.period" type="number" min="2" max="250" /></label>
    <label v-if="operand.kind === 'number'" class="field"><span>数值</span><input v-model.number="operand.value" type="number" step="any" /></label>
    <label v-else class="field"><span>引用前几根K线</span><input v-model.number="operand.offset" type="number" min="0" max="250" /></label>
    <label class="field"><span>乘数</span><input v-model.number="operand.multiplier" type="number" min="0.000001" max="1000" step="any" /></label>
  </div>
</template>

<style scoped>
.operand-editor { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; min-width: 0; }
.operand-kind { grid-column: 1 / -1; }
input, select { width: 100%; padding: 9px; border: 1px solid var(--line); border-radius: 4px; background: var(--bg); color: inherit; font: inherit; min-width: 0; }
.field { font-size: 12px; }
</style>
