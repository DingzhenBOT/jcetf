<script setup lang="ts">
import { computed, ref } from 'vue'
import Card from '@/components/ui/Card.vue'
import StatePanel from '@/components/ui/StatePanel.vue'
import Badge from '@/components/ui/Badge.vue'
import SignalRiskChart from '@/components/charts/SignalRiskChart.vue'
import BreadthChart from '@/components/charts/BreadthChart.vue'
import SignalTable from '@/components/sections/SignalTable.vue'
import WatchBoard from '@/components/sections/WatchBoard.vue'
import IndexTicker from '@/components/IndexTicker.vue'
import IndexDrawer from '@/components/IndexDrawer.vue'
import { marketState, refreshNow, secondsToRefresh } from '@/stores/market'
import { TIER_TEXT, riskLevelBadge } from '@/lib/tier'
import { fmtInt } from '@/lib/format'
import { toBeijingDate } from '@/lib/time'
import type { SignalType } from '@/api/types'

const ov = computed(() => marketState.overview)
const risk = computed(() => ov.value?.signal_risk ?? null)
const breadth = computed(() => ov.value?.breadth ?? null)
const signals = computed(() => marketState.latestSignals)
const hasSignals = computed(() => signals.value.length > 0)
const riskBadge = computed(() =>
  risk.value ? riskLevelBadge(risk.value.market_risk_level) : '',
)
const counts = computed(() => risk.value?.counts ?? {})

// 盘中 / 收盘复盘 双模式：默认按北京时间（浏览器本地，团队在国内）推断。
function defaultMode(): 'intraday' | 'review' {
  return new Date().getHours() >= 15 ? 'review' : 'intraday'
}
const mode = ref<'intraday' | 'review'>(defaultMode())

// 复盘模式：观察档位作为"明日观察候选"
const observeList = computed(() =>
  signals.value
    .filter((s) => s.signal_type === 'OBSERVE')
    .sort((a, b) => (b.score ?? 0) - (a.score ?? 0))
    .slice(0, 6),
)
const avoidCount = computed(
  () =>
    signals.value.filter((s) =>
      (['NO_PARTICIPATE', 'NO_CHASE_HIGH', 'MARKET_RISK_HIGH'] as SignalType[]).includes(
        s.signal_type,
      ),
    ).length,
)

// 指数详情抽屉：null = 关闭
const openCode = ref<string | null>(null)
</script>

<template>
  <div class="space-y-5">
    <div class="flex items-end justify-between flex-wrap gap-2">
      <div>
        <h1 class="text-xl font-semibold tracking-tight text-slate-800">市场总览</h1>
        <p class="mt-0.5 text-sm text-slate-400">
          数据截至 <span class="tnum">{{ toBeijingDate(ov?.as_of) }}</span>
          <span class="ml-2 text-slate-300">每 60 秒自动刷新 · 还 <span class="tnum">{{ secondsToRefresh }}</span> 秒</span>
        </p>
      </div>
      <div class="flex items-center gap-2">
        <!-- 盘中 / 收盘复盘 切换 -->
        <div class="inline-flex rounded-lg border border-slate-200 bg-slate-50 p-0.5 text-sm">
          <button
            class="rounded-md px-3 py-1.5 transition active:scale-[0.98]"
            :class="mode === 'intraday' ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500'"
            @click="mode = 'intraday'"
          >
            盘中
          </button>
          <button
            class="rounded-md px-3 py-1.5 transition active:scale-[0.98]"
            :class="mode === 'review' ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500'"
            @click="mode = 'review'"
          >
            收盘复盘
          </button>
        </div>
        <button
          class="rounded-md border border-slate-200 px-3 py-1.5 text-sm text-slate-600 transition hover:bg-slate-100 active:scale-[0.98]"
          @click="refreshNow()"
        >
          手动刷新
        </button>
      </div>
    </div>

    <!-- 顶部指数数字带：上证指数 hero + 其余指数红涨绿跌，点开看详情 -->
    <IndexTicker @open="openCode = $event" />

    <!-- 今日关注榜（结论前置，两种模式都显示） -->
    <Card title="今日关注榜" subtitle="按可操作度排序的 TOP 机会">
      <WatchBoard :signals="signals" />
    </Card>

    <StatePanel :loading="marketState.loading" :error="marketState.error" @retry="refreshNow()">
      <!-- 盘中：实时仪表盘 -->
      <template v-if="mode === 'intraday'">
        <div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Card title="市场宽度" subtitle="涨跌家数分布">
            <BreadthChart :breadth="breadth" />
            <div v-if="breadth" class="mt-2 grid grid-cols-3 gap-2 text-center text-xs">
              <div>
                <div class="tnum font-semibold text-up">{{ fmtInt(breadth.limit_up) }}</div>
                <div class="text-slate-400">涨停</div>
              </div>
              <div>
                <div class="tnum font-semibold text-down">{{ fmtInt(breadth.limit_down) }}</div>
                <div class="text-slate-400">跌停</div>
              </div>
              <div>
                <div class="tnum font-semibold text-slate-600">
                  {{ breadth.advance_ratio != null ? `${(breadth.advance_ratio * 100).toFixed(0)}%` : '--' }}
                </div>
                <div class="text-slate-400">上涨占比</div>
              </div>
            </div>
            <div v-else class="py-6 text-center text-sm text-slate-400">暂无宽度数据</div>
          </Card>

          <Card title="信号风险汇总" :subtitle="`覆盖 ${risk?.total ?? 0} 支 ETF`">
            <div class="mb-2 flex items-center justify-between">
              <span class="text-xs text-slate-400">市场的风险水平</span>
              <Badge v-if="risk" :text="`风险 ${risk.market_risk_level}`" :class="riskBadge" />
            </div>
            <SignalRiskChart v-if="hasSignals" :risk="risk" />
            <div v-else class="py-10 text-center text-sm text-slate-400">暂无信号</div>
            <div class="mt-2 flex flex-wrap gap-1.5">
              <span
                v-for="(cnt, key) in counts"
                :key="key"
                class="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-500"
              >
                {{ TIER_TEXT[key as keyof typeof TIER_TEXT] ?? key }}：{{ cnt }}
              </span>
            </div>
          </Card>
        </div>

        <Card class="mt-4" title="最新信号" subtitle="每支 ETF 最新一条公共信号">
          <SignalTable v-if="hasSignals" :signals="signals" show-etf />
          <div v-else class="py-8 text-center text-sm text-slate-400">暂无信号数据</div>
        </Card>
      </template>

      <!-- 收盘复盘：今日复盘 + 明日观察 -->
      <template v-else>
        <div class="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <Card title="今日复盘" subtitle="截至收盘的信号分布">
            <div v-if="risk" class="space-y-2 text-sm">
              <div class="flex items-center justify-between">
                <span class="text-slate-400">市场风险水平</span>
                <Badge :text="`风险 ${risk.market_risk_level}`" :class="riskBadge" />
              </div>
              <div class="flex items-center justify-between">
                <span class="text-slate-400">可操作</span>
                <span class="tnum font-semibold text-emerald-600">{{ (counts['OPPORTUNITY_ENHANCE'] ?? 0) + (counts['SMALL_POSITION'] ?? 0) }}</span>
              </div>
              <div class="flex items-center justify-between">
                <span class="text-slate-400">观察</span>
                <span class="tnum font-semibold text-sky-600">{{ counts['OBSERVE'] ?? 0 }}</span>
              </div>
              <div class="flex items-center justify-between">
                <span class="text-slate-400">规避</span>
                <span class="tnum font-semibold text-rose-600">{{ avoidCount }}</span>
              </div>
            </div>
            <div v-else class="py-6 text-center text-sm text-slate-400">暂无信号</div>
          </Card>

          <Card class="lg:col-span-2" title="明日观察候选" subtitle="观察档位中得分较高者">
            <ul v-if="observeList.length" class="divide-y divide-slate-100">
              <li
                v-for="s in observeList"
                :key="s.signal_id"
                class="flex items-center justify-between py-2"
              >
                <router-link
                  :to="`/etfs/${s.target_etf}`"
                  class="text-sm text-slate-700 hover:text-sky-600"
                  >{{ s.target_etf }} · {{ s.signal_type_text }}</router-link
                >
                <span class="tnum text-xs text-slate-400">{{ s.score != null ? s.score.toFixed(0) : '--' }} 分</span>
              </li>
            </ul>
            <div v-else class="py-6 text-center text-sm text-slate-400">无观察候选</div>
          </Card>
        </div>

        <Card class="mt-4" title="今日全部信号" subtitle="收盘后完整清单">
          <SignalTable v-if="hasSignals" :signals="signals" show-etf />
          <div v-else class="py-8 text-center text-sm text-slate-400">暂无信号数据</div>
        </Card>
      </template>
    </StatePanel>

    <!-- 指数详情抽屉 -->
    <IndexDrawer :code="openCode" @close="openCode = null" />
  </div>
</template>
