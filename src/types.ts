export type View = "market" | "strategy" | "backtest" | "ai";
export type Timeframe = "intraday" | "five_day" | "daily" | "weekly" | "monthly";
export type BuiltinStrategyType = "trend_filter" | "swing_breakout" | "intraday_scalp";
export type StrategyType = BuiltinStrategyType | "custom";

export interface Bar {
  datetime: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface Analysis {
  price: number;
  changePct: number;
  sma20: number;
  sma60: number;
  macd: number;
  rsi14: number;
  atr20: number;
  trend: boolean;
  momentum: boolean;
  volumeExpansion: boolean;
  breakout: boolean;
  signal: string;
  assessment: string;
}

export interface MarketSnapshot {
  symbol: string;
  timeframe: Timeframe;
  metadata: {
    source: string;
    sourceTime: string;
    exchange: string;
    sampled: boolean;
    marketOpen: boolean;
    sessionComplete: boolean;
    sessionStatus: string;
    delayNotice: string;
    analysisBasis: string;
    dayChangePct: number | null;
  };
  analysis: Analysis;
  strategy: StrategySignal;
  bars: Bar[];
}

export interface StrategySignal {
  supported: boolean;
  rules?: CustomRules;
  parameters?: Record<string, number>;
  fastPeriod?: number;
  slowPeriod?: number;
  strategyType?: StrategyType;
  signal: string;
  message: string;
  asOf: string | null;
  price?: number;
  basis?: string;
  entryCondition?: boolean;
  exitCondition?: boolean;
  barStatus?: "completed" | "in_progress";
  regime?: "bull" | "bear" | "rebound_watch" | "data_insufficient";
  action?: "hold" | "exit" | "observe";
  rebound?: boolean;
  regimeSmaPeriod?: number;
}

export interface SignalHistoryRecord {
  id: string;
  symbol: string;
  strategyName: string;
  strategyType: StrategyType;
  timeframeLabel: string;
  signal: string;
  message: string;
  sourceTime: string;
  capturedAt: string;
}

export type RuleIndicator = "close" | "open" | "high" | "low" | "volume" | "sma" | "ema" | "rsi" | "atr" | "highest" | "lowest" | "volume_sma" | "number";
export type RuleOperator = "gt" | "gte" | "lt" | "lte" | "crosses_above" | "crosses_below";
export interface RuleOperand {
  kind: RuleIndicator;
  period?: number;
  value?: number;
  offset: number;
  multiplier: number;
}
export interface RuleCondition { left: RuleOperand; operator: RuleOperator; right: RuleOperand }
export interface RuleGroup { match: "all" | "any"; conditions: RuleCondition[] }
export interface CustomRules {
  symbol: string;
  timeframe: Exclude<Timeframe, "five_day">;
  entry: RuleGroup;
  exit: RuleGroup;
  positionPct: number;
  minHoldBars: number;
  stopLossPct: number;
  takeProfitPct: number;
  trailingStopPct: number;
  closeAtSessionEnd: boolean;
}
export interface RuleOptions {
  label: string;
  indicators: { value: RuleIndicator; label: string; period: boolean }[];
  operators: { value: RuleOperator; label: string }[];
  timeframes: { value: CustomRules["timeframe"]; label: string }[];
  maxConditions: number;
}
interface StrategyIdentity {
  id: string;
  name: string;
  description: string;
  version: number;
  updatedAt: string;
}
export interface BuiltinStrategy extends StrategyIdentity {
  strategyType: BuiltinStrategyType;
  fastPeriod: number;
  slowPeriod: number;
  parameters: Record<string, number>;
}
export interface CustomStrategy extends StrategyIdentity {
  strategyType: "custom";
  rules: CustomRules;
}
export type Strategy = BuiltinStrategy | CustomStrategy;
export interface StrategyPresentation {
  label: string;
  entryRule: string;
  exitRule: string;
  entryLabel: string;
  exitLabel: string;
  executionNote: string;
  scopeLabel: string;
}

export interface StrategyParameterDefinition {
  key: string;
  label: string;
  default: number;
  min: number;
  max: number;
  step: number;
  integer: boolean;
  unit: string;
  help: string;
}

export interface StrategyTemplate {
  strategyType: BuiltinStrategyType;
  label: string;
  templateName: string;
  defaultSymbol: string;
  defaultTimeframe: Timeframe;
  defaultFastPeriod: number;
  defaultSlowPeriod: number;
  parameterDefinitions: StrategyParameterDefinition[];
  description: string;
  entryRule: string;
  exitRule: string;
  entryLabel: string;
  exitLabel: string;
  executionNote: string;
  supportedSymbols: string[];
  supportedTimeframes: Timeframe[];
  scopeLabel: string;
  scopeError: string;
  caveat: string;
}

export interface BacktestResult {
  rules?: CustomRules;
  symbol?: string;
  timeframe?: Timeframe;
  strategyType?: StrategyType;
  sampleWarning?: string;
  initialCapital: number;
  finalEquity: number;
  totalReturnPct: number;
  annualizedReturnPct: number | null;
  maxDrawdownPct: number;
  sharpe: number | null;
  winRatePct: number;
  exposurePct: number;
  tradeCount: number;
  openPosition: boolean;
  equityCurve: { time: string; value: number }[];
  bars?: Bar[];
  trades: {
    entry: string;
    exit: string;
    entryPrice: number;
    exitPrice: number;
    returnPct: number;
    pnl: number;
    transactionCost: number;
    symbol?: string;
  }[];
  openPositions?: {
    symbol?: string;
    entry: string;
    entryPrice: number;
    currentPrice: number;
    entryCapital: number;
    totalPnl: number;
    returnPct: number;
  }[];
  rollingPerformance?: {
    period: number;
    start: string;
    end: string;
    returnPct: number;
    benchmarkReturnPct: number;
    maxDrawdownPct: number;
    exposurePct: number;
    transactionCost: number;
  }[];
  caveat: string;
  benchmark?: { name: string; totalReturnPct: number; maxDrawdownPct: number; equityCurve: { time: string; value: number }[] };
  costSensitivity?: { scenario: string; totalReturnPct: number; maxDrawdownPct: number }[];
  parameters?: Record<string, number>;
  positionSizingTargetVolatilityPct?: number;
  rebalanceCount?: number;
}

export interface AISettings {
  baseUrl: string;
  model: string;
  apiKey: string;
  hasApiKey: boolean;
}

export interface TimeframeOption {
  value: Timeframe;
  label: string;
}
