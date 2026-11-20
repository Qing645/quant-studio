<script setup lang="ts">
import { computed } from "vue";
import { CircleHelp, FlaskConical, LoaderCircle, ShieldCheck } from "@lucide/vue";
import BacktestKlineChart from "./BacktestKlineChart.vue";
import EquityChart from "./EquityChart.vue";
import BaseTDetails from "./BaseTDetails.vue";
import type { BacktestResult } from "../types";

const props = defineProps<{
  strategyName: string;
  strategyType: string;
  fastPeriod?: number;
  slowPeriod?: number;
  strategyParameters: Record<string, number>;
  symbol: string;
  timeframeLabel: string;
  loading: boolean;
  form: { initialCapital: number; feeRate: number; slippageBps: number };
  result: BacktestResult | null;
  decisionMinutes?: number;
}>();
const emit = defineEmits<{ run: []; navigateStrategy: [] }>();

const backtestSourceLabel = computed(() => {
  const source = props.result?.metadata.source ?? "数据源未知";
  if (source.includes("Eastmoney")) return "东方财富前复权";
  if (source.includes("Yahoo Finance")) return "Yahoo Finance 复权";
  if (source.includes("Tencent")) return "腾讯公开行情";
  return source;
});
</script>

<template>
  <div class="page-heading"><div><div class="eyebrow">HISTORICAL SIMULATION <span class="eyebrow-line"></span> NEXT BAR EXECUTION</div><h1>策略回测</h1><p>成本可配置，信号后移执行，避免同K线前视。</p></div><button class="primary-button" :disabled="loading" @click="emit('run')"><LoaderCircle v-if="loading" :size="15" class="spinning" /><FlaskConical v-else :size="15" /> 运行回测</button></div>
  <section class="backtest-config"><div class="config-heading"><div><h2>{{ strategyName }}</h2><span>{{ strategyType === 'custom' ? `标的 ${symbol} · ${timeframeLabel} · 自定义规则` : strategyType === 'intraday_scalp' ? `标的 ${symbol} · ${decisionMinutes ?? 1}分钟决策 · 最近可用样本 · 底仓保留隔夜` : `标的 ${symbol} · 日K · 长历史前复权样本` }}</span></div><button class="subtle-button" @click="emit('navigateStrategy')">修改策略 <span>→</span></button></div><div class="config-fields"><label class="field"><span>{{ strategyType === 'intraday_scalp' ? '初始账户权益（含已有底仓）' : '初始资金' }}</span><div class="number-field"><input v-model.number="props.form.initialCapital" type="number" min="1000" step="10000" /><span>元</span></div></label><label class="field"><span>手续费率</span><div class="number-field"><input v-model.number="props.form.feeRate" type="number" min="0" max="0.02" step="0.0001" /><span>单边</span></div></label><label class="field"><span>滑点</span><div class="number-field"><input v-model.number="props.form.slippageBps" type="number" min="0" max="500" step="1" /><span>基点</span></div></label><div class="config-note"><ShieldCheck :size="15" /> 研究用途 · 不会连接交易账户</div></div></section>
  <template v-if="result">
    <div class="results-toolbar"><div><h2>回测结果</h2><span>{{ result.tradeCount }} {{ result.baseT ? `笔已完成做 T 配对 · ${result.baseT.pendingBuybackShares ? '仍有份额待买回' : '底仓保留隔夜'}` : `笔已平仓 · ${result.openPosition ? '期末持仓' : '期末空仓'}` }}</span></div><span class="data-range">{{ backtestSourceLabel }} · 截至 {{ result.metadata.sourceTime }}<template v-if="result.metadata.incompleteCurrentBarExcluded"> · 已排除未完成日K</template></span></div>
    <div class="result-metrics"><div><span>累计收益</span><b :class="result.totalReturnPct >= 0 ? 'up' : 'down'">{{ result.totalReturnPct.toFixed(2) }}%</b></div><div><span>年化收益</span><b>{{ result.annualizedReturnPct === null ? '样本不足' : `${result.annualizedReturnPct.toFixed(2)}%` }}</b></div><div><span>最大回撤</span><b class="down">{{ result.maxDrawdownPct.toFixed(2) }}%</b></div><div><span>夏普比率</span><b>{{ result.sharpe === null ? '样本不足' : result.sharpe.toFixed(2) }}</b></div><div><span>持仓暴露</span><b>{{ result.exposurePct.toFixed(1) }}%</b></div><div><span>胜率</span><b>{{ result.winRatePct.toFixed(1) }}%</b></div></div>
    <div v-if="result.benchmark" class="benchmark-strip"><span>{{ result.benchmark.name }}</span><b :class="result.benchmark.totalReturnPct >= 0 ? 'up' : 'down'">{{ result.benchmark.totalReturnPct.toFixed(2) }}%</b><span>最大回撤</span><b class="down">{{ result.benchmark.maxDrawdownPct.toFixed(2) }}%</b><span v-if="result.positionSizingTargetVolatilityPct">仓位估算目标</span><b v-if="result.positionSizingTargetVolatilityPct">{{ result.positionSizingTargetVolatilityPct.toFixed(0) }}%</b></div>
    <p v-if="result.researchTarget" class="risk-note">研究版目标：年化至少 {{ result.researchTarget.annualReturnPct }}%、最大回撤约 {{ result.researchTarget.maxDrawdownPct }}% · {{ result.researchTarget.achieved ? '本样本达到数值门槛，仍需验证稳定性' : '本样本未达到目标' }}；不代表未来收益或损失上限。</p>
    <section v-if="result.riskManagement" class="rolling-section">
      <div class="section-head"><div><h2>ATR风险与资金</h2><span>单次计划价格风险 {{ result.riskManagement.riskBudgetPct }}% · 开仓上限 {{ result.riskManagement.maxPositionPct }}% · 100份取整</span></div></div>
      <p class="rolling-note">累计成本 {{ result.riskManagement.totalTransactionCost.toFixed(2) }} 元 · 双边成交额/初始资金 {{ result.riskManagement.turnoverPct.toFixed(1) }}% · 平均每笔盈亏 {{ result.riskManagement.averageTradePnl.toFixed(2) }} 元 · 平均盈利/亏损 {{ result.riskManagement.averageWinPnl.toFixed(2) }}/{{ result.riskManagement.averageLossPnl.toFixed(2) }} 元。{{ result.riskManagement.pendingExit ? `期末仍待执行：${result.riskManagement.pendingExit}` : '期末没有待执行退出。' }}</p>
      <p class="rolling-note">初始风险线在信号日冻结，持仓风险线只上移；触线在下一交易日开盘尝试退出，跳空与费用可能使亏损超过预算。不设置固定止盈，不摊平或自动加仓。</p>
      <div class="table-wrap"><table><thead><tr><th>对照组合</th><th>累计收益</th><th class="align-right">最大回撤</th></tr></thead><tbody><tr v-for="benchmark in result.benchmarks" :key="benchmark.name"><td>{{ benchmark.name }}</td><td>{{ benchmark.totalReturnPct.toFixed(2) }}%</td><td class="align-right">{{ benchmark.maxDrawdownPct.toFixed(2) }}%</td></tr></tbody></table></div>
      <details v-if="result.riskManagement.cancelledEntries.length"><summary>取消入场 {{ result.riskManagement.cancelledEntries.length }} 次</summary><div class="table-wrap"><table><thead><tr><th>尝试日期</th><th>取消原因</th></tr></thead><tbody><tr v-for="item in result.riskManagement.cancelledEntries" :key="item.time"><td>{{ item.time.slice(0, 10) }}</td><td>{{ item.reason }}</td></tr></tbody></table></div></details>
      <details><summary>持仓风险线轨迹</summary><div class="table-wrap"><table><thead><tr><th>日期</th><th>收盘风险线</th><th>待执行退出</th></tr></thead><tbody><tr v-for="item in result.riskManagement.riskLineCurve.filter(item => item.value !== null).slice(-20).reverse()" :key="item.time"><td>{{ item.time.slice(0, 10) }}</td><td>{{ item.value?.toFixed(4) }}</td><td>{{ item.pendingExit ?? '—' }}</td></tr></tbody></table></div><p class="rolling-note">最近20根持仓日K；风险线仅对应回测模拟持仓。</p></details>
    </section>
    <section v-if="result.researchPeriods?.length" class="rolling-section">
      <div class="section-head"><div><h2>按时间分段回顾</h2><span>固定规则连续运行 · 分段统计不等于独立样本外验证</span></div></div>
      <div class="table-wrap"><table><thead><tr><th>区间</th><th>策略收益</th><th>最大回撤</th><th class="align-right">交易成本</th></tr></thead><tbody><tr v-for="period in result.researchPeriods" :key="period.label"><td>{{ period.start }} 至 {{ period.end }}</td><td>{{ period.totalReturnPct.toFixed(2) }}%</td><td>{{ period.maxDrawdownPct.toFixed(2) }}%</td><td class="align-right">{{ period.transactionCost.toFixed(2) }}</td></tr></tbody></table></div>
    </section>
    <section v-if="result.costSensitivity?.length" class="rolling-section">
      <div class="section-head"><div><h2>交易成本敏感性</h2><span>{{ result.baseT ? '按对应成本重新计算价差门槛与成交；同时查看做 T 增量' : '保持信号规则不变，仅调整手续费和滑点' }}</span></div></div>
      <div class="table-wrap"><table><thead><tr><th>成本假设</th><th>累计收益</th><th v-if="result.baseT">做 T 增量</th><th class="align-right">最大回撤</th></tr></thead><tbody>
        <tr v-for="scenario in result.costSensitivity" :key="scenario.scenario"><td>{{ scenario.scenario }}</td><td :class="scenario.totalReturnPct >= 0 ? 'up' : 'down'">{{ scenario.totalReturnPct.toFixed(2) }}%</td><td v-if="result.baseT" :class="(scenario.tReturnPct ?? 0) >= 0 ? 'up' : 'down'">{{ scenario.tReturnPct?.toFixed(3) }}%</td><td class="align-right down">{{ scenario.maxDrawdownPct.toFixed(2) }}%</td></tr>
      </tbody></table></div>
    </section>
    <section v-if="result.rollingPerformance?.length" class="rolling-section">
      <div class="section-head"><div><h2>{{ result.strategyType === 'custom' ? '分段表现' : '滚动样本外表现' }}</h2><span>{{ result.strategyType === 'custom' ? '预热后区间等分为四段 · 买入持有作为基准' : '共同样本外区间等分为四段 · 等权买入持有作为基准' }}</span></div></div>
      <div class="table-wrap"><table><thead><tr><th>区间</th><th>策略收益</th><th>持有收益</th><th>最大回撤</th><th>持仓暴露</th><th class="align-right">交易成本</th></tr></thead><tbody>
        <tr v-for="period in result.rollingPerformance" :key="period.period"><td>{{ period.start.slice(0, 10) }} 至 {{ period.end.slice(0, 10) }}</td><td :class="period.returnPct >= 0 ? 'up' : 'down'">{{ period.returnPct.toFixed(2) }}%</td><td :class="period.benchmarkReturnPct >= 0 ? 'up' : 'down'">{{ period.benchmarkReturnPct.toFixed(2) }}%</td><td class="down">{{ period.maxDrawdownPct.toFixed(2) }}%</td><td>{{ period.exposurePct.toFixed(1) }}%</td><td class="align-right">{{ period.transactionCost.toFixed(2) }}</td></tr>
      </tbody></table></div>
      <p class="rolling-note">分段结果用于观察策略表现是否稳定，不用于在同一回测样本内挑选参数。</p>
    </section>
    <BaseTDetails v-if="result.baseT" :result="result" :symbol="result.symbol ?? symbol" />
    <section class="chart-section result-chart-section backtest-kline-section">
      <div class="section-head"><div><h2>历史 K 线与成交点</h2><span>红色▲买入 · 绿色▼卖出 · 标记按实际执行价定位，悬停查看成交信息</span></div></div>
      <BacktestKlineChart v-if="result.bars?.length" :symbol="result.symbol ?? symbol" :strategy-type="result.strategyType ?? strategyType" :fast-period="fastPeriod" :slow-period="slowPeriod" :parameters="result.parameters ?? strategyParameters" :bars="result.bars" :trades="result.trades" :open-positions="result.openPositions ?? []" :executions="result.executions" />
      <div v-else class="detail-empty">本次回测未返回历史 K 线。</div>
    </section>
    <section v-if="!result.baseT" class="trade-details">
      <div class="section-head"><div><h2>成交明细</h2><span>{{ result.tradeCount }} 笔已平仓 · 执行价已计入手续费与滑点</span></div></div>
      <div v-if="result.trades.length" class="table-wrap trades-table"><table><thead><tr><th>标的</th><th>买入时间</th><th>买入执行价</th><th>卖出时间</th><th>卖出执行价</th><th>收益率</th><th class="align-right">盈亏</th></tr></thead><tbody>
        <tr v-for="(trade, index) in [...result.trades].reverse()" :key="`${trade.symbol ?? symbol}-${trade.entry}-${index}`"><td>{{ trade.symbol ?? symbol }}</td><td>{{ trade.entry.slice(0, 16) }}</td><td>{{ trade.entryPrice.toFixed(3) }}</td><td>{{ trade.exit.slice(0, 16) }}</td><td>{{ trade.exitPrice.toFixed(3) }}</td><td :class="trade.returnPct >= 0 ? 'up' : 'down'">{{ trade.returnPct.toFixed(2) }}%</td><td class="align-right">{{ trade.pnl.toFixed(2) }}</td></tr>
      </tbody></table></div>
      <div v-else class="detail-empty">当前区间没有已平仓交易。</div>
    </section>
    <section v-if="result.openPositions?.length" class="open-position-section">
      <div class="section-head"><div><h2>期末持仓</h2><span>{{ result.riskManagement ? '模拟持仓按期末收盘盯市，尚未执行的退出不视为已成交' : '收益包含月度调仓的已实现部分和期末未实现部分' }}</span></div></div>
      <div class="table-wrap"><table><thead><tr><th>标的</th><th>买入时间</th><th>买入执行价</th><th>期末价格</th><th>累计投入</th><th>累计盈亏</th><th class="align-right">收益率</th></tr></thead><tbody>
        <tr v-for="(position, index) in result.openPositions" :key="`${position.symbol ?? symbol}-${position.entry}-${index}`"><td>{{ position.symbol ?? symbol }}</td><td>{{ position.entry.slice(0, 16) }}</td><td>{{ position.entryPrice.toFixed(3) }}</td><td>{{ position.currentPrice.toFixed(3) }}</td><td>{{ position.entryCapital.toFixed(2) }}</td><td :class="position.totalPnl >= 0 ? 'up' : 'down'">{{ position.totalPnl.toFixed(2) }}</td><td class="align-right" :class="position.returnPct >= 0 ? 'up' : 'down'">{{ position.returnPct.toFixed(2) }}%</td></tr>
      </tbody></table></div>
    </section>
    <div class="chart-section result-chart-section"><div class="section-head"><div><h2>账户权益</h2><span>按收盘盯市 · 未平仓盈亏计入期末净值</span></div></div><EquityChart :result="result" /></div>
    <div class="risk-note"><CircleHelp :size="15" /> {{ result.sampleWarning ? `${result.sampleWarning} ${result.caveat}` : result.caveat }}<template v-if="result.timeframe !== 'intraday'"> 年化指标仅作参考，分钟样本不可直接与日线年化比较。</template></div>
  </template>
  <div v-else class="empty-results"><FlaskConical :size="22" /><b>运行策略以生成结果</b><span>回测区间由数据源可提供的历史K线决定。</span></div>
</template>
