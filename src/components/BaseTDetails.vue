<script setup lang="ts">
import type { BacktestResult } from "../types";

defineProps<{ result: BacktestResult; symbol: string }>();
</script>

<template>
  <section v-if="result.baseT" class="base-t-details">
    <div class="section-head"><div><h2>底仓做 T 收益与份额</h2><span>{{ result.baseT.decisionMinutes }} 分钟决策 · {{ result.baseT.decisionBarCount }} 根完整决策K · 账户收益 = 底仓持有收益 + 做 T 增量</span></div></div>
    <div class="t-metrics">
      <div><span>底仓行情盈亏</span><b :class="result.baseT.basePnl >= 0 ? 'up' : 'down'">{{ result.baseT.basePnl.toFixed(2) }} 元</b></div>
      <div><span>做 T 增量（含待买回盯市）</span><b :class="result.baseT.tPnl >= 0 ? 'up' : 'down'">{{ result.baseT.tPnl.toFixed(2) }} 元 / {{ result.baseT.tReturnPct.toFixed(3) }}%</b></div>
      <div><span>已配对做 T 净盈亏</span><b :class="result.baseT.closedTPnl >= 0 ? 'up' : 'down'">{{ result.baseT.closedTPnl.toFixed(2) }} 元</b></div>
      <div><span>手续费与滑点合计</span><b>{{ result.baseT.transactionCost.toFixed(2) }} 元</b></div>
    </div>
    <p class="t-note">{{ result.baseT.initialization }}</p>
    <p class="t-note">{{ result.caveat }}</p>
    <p class="t-note">期初底仓 {{ result.baseT.initialShares }} 份 · 当前持有 {{ result.baseT.currentShares }} 份 · 可卖旧份额 {{ result.baseT.sellableShares }} 份 · 当日买回锁定 {{ result.baseT.lockedShares }} 份 · 可用现金 {{ result.baseT.cash.toFixed(2) }} 元</p>
    <p v-if="result.baseT.pendingBuybackShares" class="t-pending">尚有 {{ result.baseT.pendingBuybackShares }} 份未买回，当前低于原底仓；机会损益已按最新价计入做 T 增量。未到恢复时点、缺报价或资金不足时，不假设已恢复底仓。</p>
    <div class="table-wrap"><table><thead><tr><th>卖出时间</th><th>旧份额卖出价</th><th>买回时间</th><th>买回执行价</th><th>份额</th><th>净价差收益率</th><th>做 T 净盈亏</th><th>买回原因</th></tr></thead><tbody>
      <tr v-for="(trade, index) in [...result.trades].reverse()" :key="`${trade.entry}-${trade.exit}-${index}`"><td>{{ trade.entry.slice(0, 16) }}</td><td>{{ trade.entryPrice.toFixed(4) }}</td><td>{{ trade.exit.slice(0, 16) }}</td><td>{{ trade.exitPrice.toFixed(4) }}</td><td>{{ trade.shares }}</td><td :class="trade.returnPct >= 0 ? 'up' : 'down'">{{ trade.returnPct.toFixed(3) }}%</td><td :class="trade.pnl >= 0 ? 'up' : 'down'">{{ trade.pnl.toFixed(2) }}</td><td>{{ trade.exitReason }}</td></tr>
    </tbody></table></div>
    <p v-if="!result.trades.length" class="t-note">暂无完成买回的做 T 配对；未配对的卖出仍计入账户净值与成交记录。</p>
    <details v-if="result.executions?.length" class="t-ledger">
      <summary>查看 {{ result.executions.length }} 笔成交与 T+1 份额账本</summary>
      <div class="table-wrap"><table><thead><tr><th>时间</th><th>标的</th><th>方向</th><th>执行价</th><th>份额</th><th>剩余可卖</th><th>当日锁定</th><th>成本</th><th>原因</th></tr></thead><tbody>
        <tr v-for="(fill, index) in result.executions" :key="`${fill.time}-${index}`"><td>{{ fill.time.slice(0, 16) }}</td><td>{{ symbol }}</td><td :class="fill.side === 'buy' ? 'up' : 'down'">{{ fill.side === 'buy' ? '买回' : '卖出旧份额' }}</td><td>{{ fill.price.toFixed(4) }}</td><td>{{ fill.shares }}</td><td>{{ fill.sellableShares }}</td><td>{{ fill.lockedShares }}</td><td>{{ fill.transactionCost.toFixed(2) }}</td><td>{{ fill.reason }}</td></tr>
      </tbody></table></div>
    </details>
  </section>
</template>

<style scoped>
.base-t-details { margin: 22px 0; padding: 20px; background: var(--panel); border: 1px solid var(--line); }
.t-metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 18px; padding: 18px 0; }
.t-metrics span, .t-metrics b { display: block; line-height: 1.7; }
.t-metrics span, .t-note { font-size: 12px; color: var(--muted); line-height: 1.8; }
.t-metrics b { font-size: 16px; font-weight: 500; }
.t-pending { color: var(--amber); line-height: 1.8; }
.t-ledger { margin-top: 18px; }
.t-ledger summary { cursor: pointer; color: var(--soft); padding: 10px 0; }
@media(max-width: 800px) { .t-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
</style>
