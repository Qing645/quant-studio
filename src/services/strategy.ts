import type {
  BuiltinStrategy, CustomStrategy, RuleCondition, RuleGroup, RuleOperand, RuleOptions,
  Strategy, StrategyPresentation, StrategyTemplate, Timeframe
} from "../types";

/** Create an independent empty definition; no built-in algorithm is selected or copied. */
export function createCustomStrategy(symbol: string, timeframe: Timeframe): CustomStrategy {
  return {
    id: "", name: "新策略", strategyType: "custom", description: "", version: 0, updatedAt: "",
    rules: {
      symbol: symbol.trim().toUpperCase().replace(/\.(SZSE|SSE|SZ|SH)$/, ""),
      timeframe: timeframe === "five_day" ? "intraday" : timeframe,
      entry: { match: "all", conditions: [] }, exit: { match: "all", conditions: [] },
      positionPct: 100, minHoldBars: 1, stopLossPct: 0, takeProfitPct: 0,
      trailingStopPct: 0, closeAtSessionEnd: false
    }
  };
}

/** Keep editable copies detached from the saved record, including nested condition groups. */
export function copyStrategy(strategy: Strategy): Strategy {
  return JSON.parse(JSON.stringify(strategy)) as Strategy;
}

/** Serialize the actual computation contract for API calls and stale-result protection. */
export function strategyExecution(strategy: Strategy) {
  return strategy.strategyType === "custom"
    ? { strategyType: strategy.strategyType, rules: strategy.rules }
    : { strategyType: strategy.strategyType, fastPeriod: strategy.fastPeriod,
        slowPeriod: strategy.slowPeriod, parameters: strategy.parameters };
}

/** Encode only executable fields, preserving the existing GET market API for built-in clients. */
export function marketStrategyQuery(strategy: Strategy): URLSearchParams {
  const query = new URLSearchParams({ strategyType: strategy.strategyType });
  if (strategy.strategyType === "custom") query.set("rules", JSON.stringify(strategy.rules));
  else {
    query.set("fastPeriod", String(strategy.fastPeriod));
    query.set("slowPeriod", String(strategy.slowPeriod));
    query.set("parameters", JSON.stringify(strategy.parameters));
  }
  return query;
}

/** Render a built-in rule preview using the exact parameters sent to its unchanged engine. */
export function formatStrategyRule(rule: string | undefined, strategy: BuiltinStrategy): string {
  const values = { fastPeriod: strategy.fastPeriod, slowPeriod: strategy.slowPeriod, ...strategy.parameters } as Record<string, number>;
  return (rule ?? "").replace(/\{(\w+)\}/g, (placeholder, key: string) => key in values ? String(values[key]) : placeholder);
}

/** Describe user-authored references using the same vocabulary published by the API. */
export function formatOperand(operand: RuleOperand, options: RuleOptions): string {
  let text = operand.kind === "number" ? String(operand.value ?? "未填写")
    : options.indicators.find((item) => item.value === operand.kind)?.label ?? operand.kind;
  if (operand.period !== undefined) text += `(${operand.period})`;
  if (operand.offset) text += `[前${operand.offset}根]`;
  if (operand.multiplier !== 1) text += ` × ${operand.multiplier}`;
  return text;
}

/** Show the exact group composition without replacing the user's rule with a template. */
export function formatGroup(group: RuleGroup, options: RuleOptions): string {
  return group.conditions.map((condition) => `${formatOperand(condition.left, options)} ${
    options.operators.find((item) => item.value === condition.operator)?.label ?? condition.operator
  } ${formatOperand(condition.right, options)}`).join(group.match === "all" ? " 且 " : " 或 ") || "尚未添加条件";
}

/** Present configured scope and conditions consistently in research and realtime views. */
export function strategyPresentation(strategy: Strategy, template: StrategyTemplate | undefined, options: RuleOptions | null): StrategyPresentation | undefined {
  if (strategy.strategyType !== "custom") return template && {
    ...template, entryRule: formatStrategyRule(template.entryRule, strategy),
    exitRule: formatStrategyRule(template.exitRule, strategy), executionNote: formatStrategyRule(template.executionNote, strategy)
  };
  if (!options) return undefined;
  const rules = strategy.rules;
  return {
    label: options.label, scopeLabel: `${rules.symbol} · ${options.timeframes.find((item) => item.value === rules.timeframe)?.label ?? rules.timeframe}`,
    entryLabel: "入场条件", exitLabel: "离场条件", entryRule: formatGroup(rules.entry, options), exitRule: formatGroup(rules.exit, options),
    executionNote: `按完成K线判断、下一根开盘执行；目标仓位${rules.positionPct}%。持有至少${rules.minHoldBars}根后允许条件退出，风险退出不受此限制。入场和离场同时满足时不新开仓。${rules.closeAtSessionEnd ? "完整交易日末按收盘价平仓。" : ""}`
  };
}

/** Require a usable number without treating cleared numeric input as zero. */
function inRange(value: number | undefined, min: number, max: number, integer = false): boolean {
  return typeof value === "number" && Number.isFinite(value) && value >= min && value <= max && (!integer || Number.isInteger(value));
}

/** Validate individual references against the backend-published indicator catalog. */
function operandValid(operand: RuleOperand, options: RuleOptions): boolean {
  const definition = options.indicators.find((item) => item.value === operand.kind);
  return Boolean(definition && inRange(operand.offset, 0, 250, true) && inRange(operand.multiplier, Number.MIN_VALUE, 1000)
    && (definition.period ? inRange(operand.period, 2, 250, true) : operand.period === undefined)
    && (operand.kind === "number" ? inRange(operand.value, -1e12, 1e12) && operand.offset === 0 : operand.value === undefined));
}

/** Reject empty/incomplete groups and enforce the shared execution limits before saving. */
export function strategyValidationError(strategy: Strategy, template: StrategyTemplate | undefined, options: RuleOptions | null = null): string {
  if (strategy.strategyType === "custom") {
    if (!options) return "规则编辑器正在加载";
    const rules = strategy.rules;
    if (!/^\d{6}$/.test(rules.symbol)) return "请填写六位标的代码";
    if (!options.timeframes.some((item) => item.value === rules.timeframe)) return "请选择策略行情周期";
    for (const [name, group] of [["入场", rules.entry], ["离场", rules.exit]] as const) {
      if (!group.conditions.length) return `请添加${name}条件`;
      if (group.conditions.length > options.maxConditions) return `${name}条件最多${options.maxConditions}条`;
      if (group.conditions.some((condition) => !operandValid(condition.left, options) || !operandValid(condition.right, options)
        || !options.operators.some((item) => item.value === condition.operator))) return `请完整填写${name}条件的周期、数值和引用根数`;
    }
    if (!inRange(rules.positionPct, Number.MIN_VALUE, 100) || !inRange(rules.minHoldBars, 1, 250, true)
      || !inRange(rules.stopLossPct, 0, 50) || !inRange(rules.takeProfitPct, 0, 200) || !inRange(rules.trailingStopPct, 0, 50))
      return "仓位或风控参数超出允许范围";
    if (rules.closeAtSessionEnd && rules.timeframe !== "intraday") return "仅分钟K策略可设置日终平仓";
    return "";
  }
  if (!template) return "策略模板尚未加载";
  if (!inRange(strategy.fastPeriod, 2, 100, true) || !inRange(strategy.slowPeriod, strategy.fastPeriod + 1, 250, true))
    return "周期需满足 2 ≤ 快线 ≤ 100，且快线 < 慢线 ≤ 250";
  for (const field of template.parameterDefinitions)
    if (!inRange(strategy.parameters[field.key], field.min, field.max, field.integer)) return `${field.label}须为${field.min}～${field.max}之间的${field.integer ? "整数" : "数值"}`;
  return "";
}

/** Add one editable comparison without borrowing conditions from an existing strategy. */
export function createCondition(): RuleCondition {
  return { left: { kind: "close", offset: 0, multiplier: 1 }, operator: "gt", right: { kind: "number", value: 0, offset: 0, multiplier: 1 } };
}
