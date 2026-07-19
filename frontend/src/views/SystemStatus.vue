<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import Card from '@/components/ui/Card.vue'
import StatePanel from '@/components/ui/StatePanel.vue'
import Badge from '@/components/ui/Badge.vue'
import { getEtfs } from '@/api/endpoints'
import { marketState } from '@/stores/market'
import { riskLevelBadge } from '@/lib/tier'
import { toBeijing, toRelative, daysSinceBeijingDate } from '@/lib/time'

interface EtfCoverage {
  total: number
  withSignal: number
  strategyVersion: string | null
}

const coverage = ref<EtfCoverage>({ total: 0, withSignal: 0, strategyVersion: null })
const loading = ref(false)
const error = ref<string | null>(null)

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const list = await getEtfs()
    const withSig = list.filter((e) => e.latest_signal).length
    const versions = new Set(list.map((e) => e.latest_signal?.strategy_version).filter(Boolean))
    coverage.value = {
      total: list.length,
      withSignal: withSig,
      strategyVersion:
        versions.size === 1 ? [...versions][0] ?? null : versions.size ? `${versions.size} 个版本` : null,
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : '未知错误'
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(
  () => marketState.lastUpdated,
  () => {
    if (marketState.connected) void load()
  },
)

const ov = computed(() => marketState.overview)
const risk = computed(() => ov.value?.signal_risk ?? null)
const breadth = computed(() => ov.value?.breadth ?? null)
const asOfDays = computed(() => daysSinceBeijingDate(ov.value?.as_of))
const freshnessText = computed(() => {
  const d = asOfDays.value
  if (d === null) return '未知'
  if (d === 0) return '今日'
  if (d === 1) return '昨日'
  return `${d} 天前`
})
</script>

<template>
  <div class="space-y-5">
    <div>
      <h1 class="text-xl font-semibold tracking-tight text-slate-800">系统状态</h1>
      <p class="text-sm text-slate-400 mt-0.5">
        连接健康与数据新鲜度（由已落地端点派生；完整系统端点将于部署阶段接入）。
      </p>
    </div>

    <StatePanel :loading="marketState.loading" :error="marketState.error" @retry="load">
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        <Card title="API 连接">
          <div class="flex items-center gap-2">
            <span
              class="w-2.5 h-2.5 rounded-full"
              :class="marketState.connected ? 'bg-emerald-500' : 'bg-rose-500'"
            />
            <span
              class="text-sm font-medium"
              :class="marketState.connected ? 'text-emerald-700' : 'text-rose-700'"
            >
              {{ marketState.connected ? '已连接' : '未连接' }}
            </span>
          </div>
          <p class="text-xs text-slate-400 mt-2">最后成功刷新：{{ toRelative(marketState.lastUpdated) }}</p>
          <p class="text-xs text-slate-400">轮询间隔：30 秒</p>
        </Card>

        <Card title="数据新鲜度">
          <div class="text-2xl font-semibold text-slate-700">{{ freshnessText }}</div>
          <p class="text-xs text-slate-400 mt-1">
            数据截至交易日 <span class="tnum">{{ toBeijing(ov?.as_of) }}</span>
          </p>
          <p v-if="asOfDays != null && asOfDays > 1" class="text-xs text-amber-600 mt-1">
            数据较旧，请检查采集任务。
          </p>
        </Card>

        <Card title="市场风险水平">
          <div v-if="risk" class="flex items-center gap-2">
            <Badge :text="`风险 ${risk.market_risk_level}`" :class="riskLevelBadge(risk.market_risk_level)" />
          </div>
          <p class="text-xs text-slate-400 mt-2">信号覆盖 {{ risk?.total ?? 0 }} 支 ETF</p>
        </Card>

        <Card title="市场宽度数据源">
          <div class="text-sm font-medium text-slate-700">{{ breadth?.data_source ?? '—' }}</div>
          <p class="text-xs text-slate-400 mt-1">宽度交易日：{{ toBeijing(breadth?.trading_date) }}</p>
        </Card>

        <Card title="策略版本">
          <div class="text-sm font-medium text-slate-700 tnum break-all">
            {{ coverage.strategyVersion ?? '—' }}
          </div>
          <p class="text-xs text-slate-400 mt-1">不可覆盖（strategy_hash）</p>
        </Card>

        <Card title="ETF 信号覆盖">
          <div class="text-2xl font-semibold text-slate-700 tnum">
            {{ coverage.withSignal }}<span class="text-base text-slate-400"> / {{ coverage.total }}</span>
          </div>
          <p class="text-xs text-slate-400 mt-1">有最新信号的 ETF 占比</p>
        </Card>
      </div>

      <Card class="mt-4" title="说明">
        <ul class="text-sm text-slate-500 space-y-1 list-disc pl-5">
          <li>
            当前页面数据来自 <span class="tnum">/api/market/overview</span>、
            <span class="tnum">/api/signals/latest</span>、<span class="tnum">/api/etfs</span>。
          </li>
          <li>后端为只读查询，无鉴权层；公网访问由 Nginx Basic Auth + HTTPS 保护。</li>
          <li>完整系统端点（数据源状态、任务运行记录、健康检查明细）将在部署阶段（P8）接入。</li>
        </ul>
      </Card>
    </StatePanel>
  </div>
</template>
