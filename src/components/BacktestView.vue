<script setup lang="ts">
import { CircleHelp, FlaskConical, LoaderCircle, ShieldCheck } from "@lucide/vue";
import BacktestKlineChart from "./BacktestKlineChart.vue";
import EquityChart from "./EquityChart.vue";
import type { BacktestResult } from "../types";

const props = defineProps<{
  strategyName: string;
  strategyType: string;
  symbol: string;
  symbols: string;
  timeframeLabel: string;
  loading: boolean;
  form: { initialCapital: number; feeRate: number; slippageBps: number };
  result: BacktestResult | null;
  sourceTime?: string;
}>();
const emit = defineEmits<{ run: []; navigateStrategy: []; "update:symbols": [value: string] }>();

/** Keep the editable pool text in the application shell between page changes. */
function updateSymbols(event: Event): void {
  emit("update:symbols", (event.target as HTMLInputElement).value);
}
</script>

<template>
  <div class="page-heading"><div><div class="eyebrow">HISTORICAL SIMULATION <span class="eyebrow-line"></span> NEXT BAR EXECUTION</div><h1>策略回测</h1><p>成本可配置，信号后移执行，避免同K线前视。</p></div><button class="primary-button" :disabled="loading" @click="emit('run')"><LoaderCircle v-if="loading" :size="15" class="spinning" /><FlaskConical v-else :size="15" /> 运行回测</button></div>
  <section class="backtest-config"><div class="config-heading"><div><h2>{{ strategyName }}</h2><span>{{ strategyType === 'ml_direction' ? '多标的日K · 历史数据逐标的独立训练' : strategyType === 'trend_filter' ? `标的 ${symbol} · 日K · 长历史前复权样本` : `标的 ${symbol} · ${timeframeLabel} · 腾讯公开历史样本` }}</span></div><button class="subtle-button" @click="emit('navigateStrategy')">修改策略 <span>→</span></button></div><div class="config-fields"><label v-if="strategyType === 'ml_direction'" class="field pool-field"><span>股票池代码 <small>逗号分隔，最多 10 个</small></span><input :value="symbols" maxlength="120" placeholder="159977, 510300, 000001" @input="updateSymbols" /></label><label class="field"><span>初始资金</span><div class="number-field"><input v-model.number="props.form.initialCapital" type="number" min="1000" step="10000" /><span>元</span></div></label><label class="field"><span>手续费率</span><div class="number-field"><input v-model.number="props.form.feeRate" type="number" min="0" max="0.02" step="0.0001" /><span>单边</span></div></label><label class="field"><span>滑点</span><div class="number-field"><input v-model.number="props.form.slippageBps" type="number" min="0" max="500" step="1" /><span>基点</span></div></label><div class="config-note"><ShieldCheck :size="15" /> 研究用途 · 不会连接交易账户</div></div></section>
  <template v-if="result">
    <div class="results-toolbar"><div><h2>回测结果</h2><span>{{ result.tradeCount }} 笔已平仓 · {{ result.openPosition ? '期末持仓' : '期末空仓' }}</span></div><span class="data-range">数据截至 {{ sourceTime ?? "最近可用" }}</span></div>
    <div class="result-metrics"><div><span>累计收益</span><b :class="result.totalReturnPct >= 0 ? 'up' : 'down'">{{ result.totalReturnPct.toFixed(2) }}%</b></div><div><span>年化收益*</span><b>{{ result.annualizedReturnPct.toFixed(2) }}%</b></div><div><span>最大回撤</span><b class="down">{{ result.maxDrawdownPct.toFixed(2) }}%</b></div><div><span>夏普比率</span><b>{{ result.sharpe.toFixed(2) }}</b></div><div><span>持仓暴露</span><b>{{ result.exposurePct.toFixed(1) }}%</b></div><div><span>胜率</span><b>{{ result.winRatePct.toFixed(1) }}%</b></div></div>
    <div v-if="result.benchmark" class="benchmark-strip"><span>{{ result.benchmark.name }}</span><b :class="result.benchmark.totalReturnPct >= 0 ? 'up' : 'down'">{{ result.benchmark.totalReturnPct.toFixed(2) }}%</b><span>最大回撤</span><b class="down">{{ result.benchmark.maxDrawdownPct.toFixed(2) }}%</b><span v-if="result.positionSizingTargetVolatilityPct">仓位估算目标</span><b v-if="result.positionSizingTargetVolatilityPct">{{ result.positionSizingTargetVolatilityPct.toFixed(0) }}%</b></div>
    <section v-if="result.machineLearning" class="ml-evaluation"><div><span>样本外准确率</span><b>{{ result.machineLearning.accuracyPct.toFixed(1) }}%</b></div><div><span>平衡准确率</span><b>{{ result.machineLearning.balancedAccuracyPct.toFixed(1) }}%</b></div><div><span>Brier 分数</span><b>{{ result.machineLearning.brierScore.toFixed(3) }}</b></div><div><span>最新上涨概率</span><b>{{ result.machineLearning.latestUpProbabilityPct === null ? "样本不足" : `${result.machineLearning.latestUpProbabilityPct.toFixed(1)}%` }}</b></div><div><span>样本外区间 · {{ result.machineLearning.outOfSampleSamples }} 根</span><b>{{ result.machineLearning.outOfSampleStart.slice(0, 10) }} 至 {{ result.machineLearning.outOfSampleEnd.slice(0, 10) }}</b></div><p>{{ result.machineLearning.caveat }}</p></section>
    <section v-if="result.rollingPerformance?.length" class="rolling-section">
      <div class="section-head"><div><h2>滚动样本外表现</h2><span>共同样本外区间等分为四段 · 等权买入持有作为基准</span></div></div>
      <div class="table-wrap"><table><thead><tr><th>区间</th><th>策略收益</th><th>持有收益</th><th>最大回撤</th><th>持仓暴露</th><th class="align-right">交易成本</th></tr></thead><tbody>
        <tr v-for="period in result.rollingPerformance" :key="period.period"><td>{{ period.start.slice(0, 10) }} 至 {{ period.end.slice(0, 10) }}</td><td :class="period.returnPct >= 0 ? 'up' : 'down'">{{ period.returnPct.toFixed(2) }}%</td><td :class="period.benchmarkReturnPct >= 0 ? 'up' : 'down'">{{ period.benchmarkReturnPct.toFixed(2) }}%</td><td class="down">{{ period.maxDrawdownPct.toFixed(2) }}%</td><td>{{ period.exposurePct.toFixed(1) }}%</td><td class="align-right">{{ period.transactionCost.toFixed(2) }}</td></tr>
      </tbody></table></div>
      <p class="rolling-note">分段结果用于观察策略表现是否稳定，不用于在同一回测样本内挑选参数。</p>
    </section>
    <div v-if="result.symbolResults?.length" class="table-wrap symbol-results"><table><thead><tr><th>标的</th><th>样本外准确率</th><th>最新上涨概率</th><th>交易数</th><th class="align-right">标的收益</th></tr></thead><tbody><tr v-for="item in result.symbolResults" :key="item.symbol"><td>{{ item.symbol }}</td><td>{{ item.machineLearning.accuracyPct.toFixed(1) }}%</td><td>{{ item.machineLearning.latestUpProbabilityPct === null ? "—" : `${item.machineLearning.latestUpProbabilityPct.toFixed(1)}%` }}</td><td>{{ item.tradeCount }}{{ item.openPosition ? " · 持仓" : "" }}</td><td class="align-right" :class="item.totalReturnPct >= 0 ? 'up' : 'down'">{{ item.totalReturnPct.toFixed(2) }}%</td></tr></tbody></table></div>
    <div v-if="result.excludedSymbols?.length" class="risk-note excluded-symbols"><CircleHelp :size="15" /> 未纳入组合：{{ result.excludedSymbols.map((item) => `${item.symbol}（${item.reason}）`).join("；") }}</div>
    <section class="chart-section result-chart-section backtest-kline-section">
      <div class="section-head"><div><h2>历史 K 线与成交点</h2><span>红色▲买入 · 绿色▼卖出 · 标记按实际执行价定位，悬停查看成交信息</span></div></div>
      <div v-if="result.symbolResults?.length" class="backtest-symbol-charts">
        <div v-for="item in result.symbolResults" :key="item.symbol" class="backtest-symbol-chart">
          <div class="backtest-symbol-heading"><b>{{ item.symbol }}</b><span>{{ timeframeLabel }} · {{ result.symbolBars?.[item.symbol]?.length ?? 0 }} 根K线</span></div>
          <BacktestKlineChart v-if="result.symbolBars?.[item.symbol]?.length" :symbol="item.symbol" :bars="result.symbolBars[item.symbol]" :trades="result.trades.filter((trade) => trade.symbol === item.symbol)" :open-positions="(result.openPositions ?? []).filter((position) => position.symbol === item.symbol)" />
          <div v-else class="detail-empty">本次回测未返回该标的的历史 K 线。</div>
        </div>
      </div>
      <BacktestKlineChart v-else-if="result.bars?.length" :symbol="result.symbol ?? symbol" :bars="result.bars" :trades="result.trades" :open-positions="result.openPositions ?? []" />
      <div v-else class="detail-empty">本次回测未返回历史 K 线。</div>
    </section>
    <section class="trade-details">
      <div class="section-head"><div><h2>成交明细</h2><span>{{ result.tradeCount }} 笔已平仓 · 执行价已计入手续费与滑点</span></div></div>
      <div v-if="result.trades.length" class="table-wrap trades-table"><table><thead><tr><th>标的</th><th>买入时间</th><th>买入执行价</th><th>卖出时间</th><th>卖出执行价</th><th>收益率</th><th class="align-right">盈亏</th></tr></thead><tbody>
        <tr v-for="(trade, index) in [...result.trades].reverse()" :key="`${trade.symbol ?? symbol}-${trade.entry}-${index}`"><td>{{ trade.symbol ?? symbol }}</td><td>{{ trade.entry.slice(0, 16) }}</td><td>{{ trade.entryPrice.toFixed(3) }}</td><td>{{ trade.exit.slice(0, 16) }}</td><td>{{ trade.exitPrice.toFixed(3) }}</td><td :class="trade.returnPct >= 0 ? 'up' : 'down'">{{ trade.returnPct.toFixed(2) }}%</td><td class="align-right">{{ trade.pnl.toFixed(2) }}</td></tr>
      </tbody></table></div>
      <div v-else class="detail-empty">当前区间没有已平仓交易。</div>
    </section>
    <section v-if="result.openPositions?.length" class="open-position-section">
      <div class="section-head"><div><h2>期末持仓</h2><span>收益包含月度调仓的已实现部分和期末未实现部分</span></div></div>
      <div class="table-wrap"><table><thead><tr><th>标的</th><th>买入时间</th><th>买入执行价</th><th>期末价格</th><th>累计投入</th><th>累计盈亏</th><th class="align-right">收益率</th></tr></thead><tbody>
        <tr v-for="(position, index) in result.openPositions" :key="`${position.symbol ?? symbol}-${position.entry}-${index}`"><td>{{ position.symbol ?? symbol }}</td><td>{{ position.entry.slice(0, 16) }}</td><td>{{ position.entryPrice.toFixed(3) }}</td><td>{{ position.currentPrice.toFixed(3) }}</td><td>{{ position.entryCapital.toFixed(2) }}</td><td :class="position.totalPnl >= 0 ? 'up' : 'down'">{{ position.totalPnl.toFixed(2) }}</td><td class="align-right" :class="position.returnPct >= 0 ? 'up' : 'down'">{{ position.returnPct.toFixed(2) }}%</td></tr>
      </tbody></table></div>
    </section>
    <div class="chart-section result-chart-section"><div class="section-head"><div><h2>账户权益</h2><span>按收盘盯市 · 未平仓盈亏计入期末净值</span></div></div><EquityChart :result="result" /></div>
    <div class="risk-note"><CircleHelp :size="15" /> {{ result.caveat }} 年化指标仅作参考，分钟样本不可直接与日线年化比较。</div>
  </template>
  <div v-else class="empty-results"><FlaskConical :size="22" /><b>运行策略以生成结果</b><span>回测区间由数据源可提供的历史K线决定。</span></div>
</template>
