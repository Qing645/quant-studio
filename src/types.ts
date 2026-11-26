export type View = "market" | "strategy" | "backtest" | "ai";
export type Timeframe = "intraday" | "five_day" | "daily" | "weekly" | "monthly";
export type BuiltinStrategyType = "trend_filter" | "swing_breakout" | "intraday_scalp" | "star50_breakout" | "star50_pullback" | "star50_channel";
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
  tradingMode?: "base_t";
  costAssumption?: { feeRate: number; slippageBps: number };
  decisionMinutes?: number;
  decisionBarCount?: number;
  signal: string;
  message: string;
  asOf: string | null;
  price?: number;
  basis?: string;
  entryCondition?: boolean;
  exitCondition?: boolean;
  intradayPreview?: {
    asOf: string;
    price?: number;
    ready: boolean;
    entryCondition: boolean;
    exitCondition: boolean;
    blockers: string[];
    details: (NonNullable<StrategySignal["riskReference"]> & {
      priorBreakoutHigh: number;
      priorExitLow: number;
      trendMean: number;
      priorTrendMean: number;
      trendSlopePct: number;
      volumeRatio: number;
      volumeThreshold: number;
      breakoutGapPct: number;
      exitBufferPct: number;
    }) | null;
    message: string;
  };
  barStatus?: "completed" | "in_progress";
  regime?: "bull" | "bear" | "rebound_watch" | "data_insufficient";
  action?: "hold" | "exit" | "observe";
  rebound?: boolean;
  regimeSmaPeriod?: number;
  positionRiskAvailable?: boolean;
  riskReference?: {
    atr20: number;
    initialStopReference: number;
    priorBreakoutHigh?: number;
    priorExitLow?: number;
    trendMean?: number;
    priorTrendMean?: number;
    trendPassed?: boolean;
    trendSlopePassed?: boolean;
    volumeRatio?: number;
    breakoutPassed?: boolean;
    volumePassed?: boolean;
    rsi2?: number;
    oversoldPassed?: boolean;
    recoveryMean?: number;
    riskBudgetPct: number;
    maxPositionPct: number;
  } | null;
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
  options?: { value: number; label: string }[];
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
  researchTarget?: { annualReturnPct: number; maxDrawdownPct: number; achieved: boolean };
  metadata: {
    source: string;
    sourceTime: string;
    adjustment?: string;
    incompleteCurrentBarExcluded?: boolean;
  };
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
    direction?: "sell_buy";
    shares?: number;
    exitReason?: string;
    symbol?: string;
  }[];
  executions?: {
    time: string;
    side: "sell" | "buy";
    price: number;
    rawPrice: number;
    shares: number;
    reason: string;
    transactionCost: number;
    sellableShares: number;
    lockedShares: number;
    cash: number;
  }[];
  baseT?: {
    decisionMinutes: number;
    decisionBarCount: number;
    initialShares: number;
    initialCash: number;
    currentShares: number;
    sellableShares: number;
    lockedShares: number;
    cash: number;
    pendingBuybackShares: number;
    basePnl: number;
    tPnl: number;
    tReturnPct: number;
    closedTPnl: number;
    transactionCost: number;
    initialization: string;
  };
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
  costSensitivity?: { scenario: string; totalReturnPct: number; maxDrawdownPct: number; tReturnPct?: number }[];
  parameters?: Record<string, number>;
  positionSizingTargetVolatilityPct?: number;
  rebalanceCount?: number;
  benchmarks?: NonNullable<BacktestResult["benchmark"]>[];
  riskManagement?: {
    riskBudgetPct: number;
    maxPositionPct: number;
    cancelledEntries: { time: string; reason: string }[];
    pendingExit: string | null;
    riskLineCurve: { time: string; value: number | null; pendingExit: string | null }[];
    totalTransactionCost: number;
    averageTradePnl: number;
    averageWinPnl: number;
    averageLossPnl: number;
    turnoverPct: number;
  };
  researchPeriods?: { label: string; start: string; end: string; totalReturnPct: number; maxDrawdownPct: number; transactionCost: number }[];
}

export interface AIReport {
  analysis: string;
  completionStatus: "complete" | "truncated";
  generatedAt: string;
  marketContextAsOf: string;
  targetSourceTime: string;
  sources: { id: string; label: string; url: string; asOf: string }[];
  warnings: string[];
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
