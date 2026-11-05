<script setup lang="ts">
import type { BuiltinStrategy, StrategyTemplate } from "../types";

defineProps<{ strategy: BuiltinStrategy; template: StrategyTemplate }>();
</script>

<template>
  <label v-for="field in template.parameterDefinitions" :key="field.key" class="field">
    <span>{{ field.label }}</span>
    <div class="number-field">
      <input v-model.number="strategy.parameters[field.key]" type="number" :min="field.min" :max="field.max" :step="field.step" />
      <span>{{ field.unit }}</span>
    </div>
    <small class="parameter-help">{{ field.help }}</small>
  </label>
</template>

<style scoped>
.parameter-help { color: var(--muted); font-size: 12px; line-height: 1.7; }
</style>
