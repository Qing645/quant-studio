export type View = "market" | "strategy" | "backtest" | "ai";
export type Timeframe = "intraday" | "five_day" | "daily" | "weekly" | "monthly";
export type StrategyType = "trend_filter" | "swing_breakout" | "intraday_scalp";

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

export interface Strategy {
  id: string;
  name: string;
  strategyType: StrategyType;
  fastPeriod: number;
  slowPeriod: number;
  description: string;
  version: number;
  updatedAt: string;
}

export interface StrategyTemplate {
  strategyType: StrategyType;
  label: string;
  templateName: string;
  defaultSymbol: string;
  defaultTimeframe: Timeframe;
  defaultFastPeriod: number;
  defaultSlowPeriod: number;
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
