<script setup lang="ts">
import type { BuiltinStrategy, StrategyTemplate } from "../types";

defineProps<{ strategy: BuiltinStrategy; template: StrategyTemplate }>();
</script>

<template>
  <label v-for="field in template.parameterDefinitions" :key="field.key" class="field">
    <span>{{ field.label }}</span>
    <select v-if="field.options" v-model.number="strategy.parameters[field.key]">
      <option v-for="option in field.options" :key="option.value" :value="option.value">{{ option.label }}</option>
    </select>
    <div v-else class="number-field">
      <input v-model.number="strategy.parameters[field.key]" type="number" :min="field.min" :max="field.max" :step="field.step" />
      <span>{{ field.unit }}</span>
    </div>
    <small class="parameter-help">{{ field.help }}</small>
  </label>
</template>

<style scoped>
.parameter-help { color: var(--muted); font-size: 12px; line-height: 1.7; }
select { min-width: 0; width: 100%; padding: 10px; color: inherit; background: var(--panel); border: 1px solid var(--line); border-radius: 4px; font: inherit; }
</style>
