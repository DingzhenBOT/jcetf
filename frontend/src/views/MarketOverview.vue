<script setup lang="ts">
import { computed } from 'vue'
import Card from '@/components/ui/Card.vue'
import StatePanel from '@/components/ui/StatePanel.vue'
import Badge from '@/components/ui/Badge.vue'
import SignalRiskChart from '@/components/charts/SignalRiskChart.vue'
import BreadthChart from '@/components/charts/BreadthChart.vue'
import IndexBars from '@/components/charts/IndexBars.vue'
import SignalTable from '@/components/sections/SignalTable.vue'
import { marketState, refreshNow } from '@/stores/market'
import { TIER_TEXT, riskLevelBadge } from '@/lib/tier'
import { fmtInt } from '@/lib/format'
import { toBeijingDate, toRelative } from '@/lib/time'
import type { SignalType } from '@/api/types'

const ov = computed(() => marketState.overview)
const risk = computed(() => ov.value?.signal_risk ?? null)
const indices = computed(() => ov.value?.indices ?? [])
const breadth = computed(() => ov.value?.breadth ?? null)
const signals = computed(() => marketState.latestSignals)
const hasSignals = computed(() => signals.value.length > 0)
const riskBadge = computed(() =>
  risk.value ? riskLevelBadge(risk.value.market_risk_level) : '',
)
const counts = computed(() => risk.value?.counts ?? {})
</script>

<template>
  <div class="space-y-5">
    <div class="flex items-end justify-between flex-wrap gap-2">
      <div>
        <h1 class="text-xl font-semibold tracking-tight text-slate-800">市场总览</h1>
        <p class="text-sm text-slate-400 mt-0.5">
          数据截至 <span class="tnum">{{ toBeijingDate(ov?.as_of) }}</span>
          <span class="ml-2 text-slate-300">每 30 秒自动刷新 · {{ toRelative(marketState.lastUpdated) }}</span>
        </p>
      </div>
      <button
        class="text-sm px-3 py-1.5 rounded-md border border-slate-200 text-slate-600 hover:bg-slate-100 transition active:scale-[0.98]"
        @click="refreshNow()"
      >
        手动刷新
      </button>
    </div>

    <StatePanel :loading="marketState.loading" :error="marketState.error" @retry="refreshNow()">
      <!-- 指数 / 宽度 / 风险 三栏 -->
      <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card title="主要指数" subtitle="宽基最新收盘涨跌">
          <IndexBars :indices="indices" />
          <div v-if="indices.length === 0" class="text-sm text-slate-400 text-center py-6">
            暂无指数数据（观察期）
          </div>
        </Card>

        <Card title="市场宽度" subtitle="涨跌家数分布">
          <BreadthChart :breadth="breadth" />
          <div v-if="breadth" class="grid grid-cols-3 gap-2 mt-2 text-center text-xs">
            <div>
              <div class="tnum text-up font-semibold">{{ fmtInt(breadth.limit_up) }}</div>
              <div class="text-slate-400">涨停</div>
            </div>
            <div>
              <div class="tnum text-down font-semibold">{{ fmtInt(breadth.limit_down) }}</div>
              <div class="text-slate-400">跌停</div>
            </div>
            <div>
              <div class="tnum text-slate-600 font-semibold">
                {{ breadth.advance_ratio != null ? `${(breadth.advance_ratio * 100).toFixed(0)}%` : '--' }}
              </div>
              <div class="text-slate-400">上涨占比</div>
            </div>
          </div>
          <div v-else class="text-sm text-slate-400 text-center py-6">暂无宽度数据</div>
        </Card>

        <Card title="信号风险汇总" :subtitle="`覆盖 ${risk?.total ?? 0} 支 ETF`">
          <div class="flex items-center justify-between mb-2">
            <span class="text-xs text-slate-400">市场的风险水平</span>
            <Badge v-if="risk" :text="`风险 ${risk.market_risk_level}`" :class="riskBadge" />
          </div>
          <SignalRiskChart v-if="hasSignals" :risk="risk" />
          <div v-else class="text-sm text-slate-400 text-center py-10">暂无信号</div>
          <div class="flex flex-wrap gap-1.5 mt-2">
            <span
              v-for="(cnt, key) in counts"
              :key="key"
              class="text-xs px-2 py-0.5 rounded-full bg-slate-100 text-slate-500"
            >
              {{ TIER_TEXT[key as keyof typeof TIER_TEXT] ?? key }}：{{ cnt }}
            </span>
          </div>
        </Card>
      </div>

      <!-- 最新信号表 -->
      <Card class="mt-4" title="最新信号" subtitle="每支 ETF 最新一条公共信号">
        <SignalTable v-if="hasSignals" :signals="signals" show-etf />
        <div v-else class="text-sm text-slate-400 text-center py-8">暂无信号数据</div>
      </Card>
    </StatePanel>
  </div>
</template>
