<script setup lang="ts">
import { watch } from "vue";
import RuleGroupEditor from "./RuleGroupEditor.vue";
import type { CustomRules, RuleOptions } from "../types";

const props = defineProps<{ rules: CustomRules; options: RuleOptions }>();

/** Clear a minute-only option when changing to a daily or longer strategy basis. */
watch(() => props.rules.timeframe, (timeframe) => {
  if (timeframe !== "intraday") props.rules.closeAtSessionEnd = false;
});
</script>

<template>
  <div class="custom-rule-editor">
    <div class="form-grid scope-fields">
      <label class="field"><span>策略标的</span><input v-model.trim="rules.symbol" maxlength="6" placeholder="六位股票或ETF代码" inputmode="numeric" /></label>
      <label class="field"><span>策略行情周期</span><select v-model="rules.timeframe"><option v-for="item in options.timeframes" :key="item.value" :value="item.value">{{ item.label }}</option></select></label>
    </div>
    <p class="editor-help">“引用前几根K线”填0代表信号K线，填1代表它的前一根。设置突破条件时，区间最高价通常引用前1根，以排除信号K线自身；乘数可用于成交量放大等条件。</p>
    <RuleGroupEditor title="入场条件" :group="rules.entry" :options="options" />
    <RuleGroupEditor title="离场条件" :group="rules.exit" :options="options" />
    <section class="risk-settings">
      <h3>仓位与风控</h3>
      <div class="form-grid">
        <label class="field"><span>开仓资金比例（%）</span><input v-model.number="rules.positionPct" type="number" min="0.01" max="100" step="any" /></label>
        <label class="field"><span>条件退出最短持有根数</span><input v-model.number="rules.minHoldBars" type="number" min="1" max="250" /></label>
        <label class="field"><span>止损（%，0为关闭）</span><input v-model.number="rules.stopLossPct" type="number" min="0" max="50" step="any" /></label>
        <label class="field"><span>止盈（%，0为关闭）</span><input v-model.number="rules.takeProfitPct" type="number" min="0" max="200" step="any" /></label>
        <label class="field"><span>持仓收盘高点回撤（%，0为关闭）</span><input v-model.number="rules.trailingStopPct" type="number" min="0" max="50" step="any" /></label>
        <label v-if="rules.timeframe === 'intraday'" class="session-policy"><input v-model="rules.closeAtSessionEnd" type="checkbox" /> 完整交易日结束时平仓</label>
      </div>
      <p class="editor-help">止损、止盈和跟踪回撤按完成K线的收盘价判断，下一根开盘执行，不是盘中触价成交；风险退出不受最短持有根数限制。这里只做多，按所设比例开仓，剩余资金保留现金。</p>
    </section>
  </div>
</template>

<style scoped>
.scope-fields { margin-top: 0; }
.field { font-size: 12px; }
input:not([type="checkbox"]), select { padding: 9px 10px; width: 100%; min-width: 0; border: 1px solid var(--line); background: var(--bg); border-radius: 4px; color: inherit; font: inherit; }
.editor-help { color: var(--muted); font-size: 12px; line-height: 1.8; margin: 12px 0 0; }
.risk-settings { margin-top: 22px; border-top: 1px solid var(--line); padding-top: 18px; }
h3 { font-size: 14px; margin: 0; }
.session-policy { display: flex; gap: 9px; align-items: center; color: var(--soft); font-size: 12px; }
</style>
