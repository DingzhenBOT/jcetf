<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import Card from '@/components/ui/Card.vue'
import StatePanel from '@/components/ui/StatePanel.vue'
import Badge from '@/components/ui/Badge.vue'
import SignalTable from '@/components/sections/SignalTable.vue'
import OpinionList from '@/components/sections/OpinionList.vue'
import CandlestickChart from '@/components/charts/CandlestickChart.vue'
import IntradayChart from '@/components/charts/IntradayChart.vue'
import GaugeChart from '@/components/charts/GaugeChart.vue'
import { getEtfs, getOpinions, getSignalsHistory, getEtfHistory, getIntraday, refreshSignal } from '@/api/endpoints'
import type { EtfHistory, EtfListItem, Intraday, Opinion, Signal } from '@/api/types'
import { TIER_BADGE, TIER_BORDER, regimeText, phaseText, isIntradayPhase } from '@/lib/tier'
import { fmtConfidence, confidenceLevel } from '@/lib/format'
import { toBeijing, daysSinceBeijingDate } from '@/lib/time'

const route = useRoute()
const code = computed(() => String(route.params.code))

// 当日（浏览器本地=北京时间）交易日，分时接口按此取当日数据，配合后端清理只画当日
const today = computed(() => {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
})

const etf = ref<EtfListItem | null>(null)
const opinions = ref<Opinion[]>([])
const history = ref<Signal[]>([])
const loading = ref(false)
const error = ref<string | null>(null)

// 图表数据（独立加载，失败不阻断信号/意见展示）
const etfHistory = ref<EtfHistory | null>(null)
const intraday = ref<Intraday | null>(null)
const chartLoading = ref(false)
const chartError = ref<string | null>(null)

// 核心信号/意见数据（盘中实时，由短轮询刷新）
async function fetchCore(): Promise<void> {
  const [list, op, hist] = await Promise.all([
    getEtfs(),
    getOpinions(code.value),
    getSignalsHistory({ etf_code: code.value, limit: 20 }),
  ])
  etf.value = list.find((e) => e.etf_code === code.value) ?? null
  opinions.value = op.items
  history.value = hist.items
}

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    await fetchCore()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '未知错误'
  } finally {
    loading.value = false
  }
  void loadCharts()
}

// 盘中详情页 60s 短轮询（其余页面为 5min）：仅静默刷新信号/意见，不重载图表、不闪骨架屏。
async function poll(): Promise<void> {
  try {
    await fetchCore()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '未知错误'
  }
}

// 走势图（日线历史）+ 盘中分时（独立、非致命）
async function loadCharts(): Promise<void> {
  chartLoading.value = true
  chartError.value = null
  try {
    const [hist, intra] = await Promise.all([
      getEtfHistory(code.value, 120),
      getIntraday('etf', code.value, today.value),
    ])
    etfHistory.value = hist
    intraday.value = intra
  } catch (e) {
    chartError.value = e instanceof Error ? e.message : '图表加载失败'
    etfHistory.value = null
    intraday.value = null
  } finally {
    chartLoading.value = false
  }
}

onMounted(load)
watch(code, load)

// 盘中详情页：60s 短轮询保持实时（与全局 5min 轮询区分）
const timer = window.setInterval(poll, 60_000)
onBeforeUnmount(() => window.clearInterval(timer))

const missingRules = computed(() =>
  etf.value?.latest_signal?.failed_rules?.filter((r) => r.includes('missing')) ?? [],
)

// 人话结论（置顶 Hero 用）：盘中建议为主（盘前/午间/收盘前优先），收盘后复盘单独成区。
const intradayOpinions = computed<Opinion[]>(() =>
  opinions.value.filter((o) => isIntradayPhase(o.phase)),
)
const postCloseOpinions = computed<Opinion[]>(() =>
  opinions.value.filter((o) => o.phase === 'post_close'),
)
// 午盘意见（lunch 阶段，C23）
const lunchOpinions = computed<Opinion[]>(() =>
  opinions.value.filter((o) => o.phase === 'lunch'),
)

// 最新信号（live）的盘中强度/倾向（来自 Signal.supporting_metrics，C23）
const liveStrength = computed(() => {
  const sm = etf.value?.latest_signal?.supporting_metrics as Record<string, any> | undefined
  if (!sm) return null
  const score = sm.intraday_strength
  const lean = sm.intraday_lean
  if (score == null && !lean) return null
  return {
    score: typeof score === 'number' ? score : null,
    lean: typeof lean === 'string' ? lean : null,
    r1: !!sm.r1_signal,
    r2: !!sm.r2_signal,
  }
})

// 「重新评估」按钮：按需重算盘中实时信号（C23）
const refreshing = ref(false)
const refreshError = ref<string | null>(null)
async function onRefresh(): Promise<void> {
  refreshing.value = true
  refreshError.value = null
  try {
    await refreshSignal(code.value)
    await fetchCore()
  } catch (e) {
    refreshError.value = e instanceof Error ? e.message : '刷新失败'
  } finally {
    refreshing.value = false
  }
}
// 主建议：优先最新盘中意见；若无盘中意见则回退到任意最新意见（可能已是收盘后）。
const primaryOpinion = computed<Opinion | null>(() => {
  if (intradayOpinions.value.length) return intradayOpinions.value[0]
  return opinions.value[0] ?? null
})

const heroSentence = computed(() => {
  const first = primaryOpinion.value?.content
  if (first) return first
  const s = etf.value?.latest_signal
  return s?.one_liner ?? s?.suggested_action ?? ''
})

// 主建议阶段标注：盘中（含时间）/ 收盘后（次日建议）。
const primaryPhaseText = computed(() => {
  const o = primaryOpinion.value
  if (!o) return ''
  const t = toBeijing(o.generated_at)
  if (o.phase === 'post_close') return `收盘后复盘 · ${t}（供次日参考）`
  return `盘中建议 · ${t}`
})

// 信号时效提示：生成距今天数 >=1 即提示「已 N 天未更新」（可能 worker 未跑）
const signalStaleText = computed(() => {
  const sig = etf.value?.latest_signal
  if (!sig) return ''
  const d = daysSinceBeijingDate(sig.generated_at)
  return d != null && d >= 2 ? ` · ⚠ 已 ${d} 天未更新` : ''
})
</script>

<template>
  <div class="space-y-5">
    <StatePanel :loading="loading" :error="error" @retry="load">
      <template v-if="etf">
        <!-- 头部 -->
        <div class="flex items-center justify-between flex-wrap gap-3 mb-4">
          <div class="min-w-0">
            <div class="flex items-center gap-2 flex-wrap">
              <h1 class="text-xl font-semibold tracking-tight text-slate-800">{{ etf.etf_code }}</h1>
              <span class="text-slate-500">{{ etf.etf_name ?? '' }}</span>
              <span
                v-if="etf.category"
                class="text-xs px-2 py-0.5 rounded-full bg-slate-100 text-slate-500"
              >
                {{ etf.category }}
              </span>
            </div>
            <p v-if="etf.related_index_code" class="text-xs text-slate-400 mt-1">
              关联指数：{{ etf.related_index_code }}
            </p>
          </div>
          <div v-if="etf.latest_signal" class="flex flex-col items-end gap-2">
            <Badge
              :text="etf.latest_signal.signal_type_text"
              :class="TIER_BADGE[etf.latest_signal.signal_type]"
            />
            <router-link
              :to="{ path: '/portfolio', query: { etf: etf.etf_code } }"
              class="text-xs px-2.5 py-1 rounded-md border border-slate-200 text-slate-600 hover:bg-slate-50 whitespace-nowrap"
            >
              在持仓分析中查看
            </router-link>
          </div>
        </div>

        <!-- 结论 Hero（人话置顶，盘中建议为主 + 阶段/时间标注） -->
        <Card
          v-if="etf.latest_signal"
          class="border-l-4"
          :class="TIER_BORDER[etf.latest_signal.signal_type]"
        >
          <div class="flex items-start justify-between gap-3 flex-wrap">
            <div class="min-w-0">
              <div class="flex items-center gap-2 flex-wrap">
                <Badge
                  :text="etf.latest_signal.signal_type_text"
                  :class="TIER_BADGE[etf.latest_signal.signal_type]"
                />
                <span
                  v-if="primaryPhaseText"
                  class="text-xs px-2 py-0.5 rounded-full bg-slate-100 text-slate-500 tnum"
                >
                  {{ primaryPhaseText }}
                </span>
              </div>
              <p class="mt-2 text-base leading-relaxed text-slate-700">{{ heroSentence }}</p>
            </div>
            <div class="text-right shrink-0">
              <div class="text-xs text-slate-400">建议仓位</div>
              <div class="font-semibold text-slate-700">{{ etf.latest_signal.position_text }}</div>
              <div class="mt-1 text-xs text-slate-400">
                可信度：{{ confidenceLevel(etf.latest_signal.confidence) }}
              </div>
            </div>
          </div>
          <p
            v-if="primaryOpinion && primaryOpinion.phase === 'post_close'"
            class="mt-2 text-xs text-slate-400 bg-slate-50 border border-slate-100 rounded-md px-2.5 py-1.5"
          >
            当前主建议为收盘后复盘（针对次日），盘中实时建议见下方「盘中意见」或当日更早记录。
          </p>
        </Card>

        <!-- 走势 + 分时图 -->
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
          <!-- 日 K 线（开/高/低/收，可横向缩放，红涨绿跌） -->
          <Card
            :title="`日 K 线`"
            :subtitle="etfHistory ? `近 ${etfHistory.points.length} 个交易日 · 可拖动下方滑块缩放` : ''"
          >
            <div v-if="chartLoading" class="py-10 flex flex-col items-center gap-2 text-slate-400">
              <span class="w-5 h-5 border-2 border-slate-300 border-t-slate-500 rounded-full animate-spin" />
              <span class="text-xs">加载走势…</span>
            </div>
            <div
              v-else-if="chartError"
              class="py-8 text-center text-xs text-rose-600 bg-rose-50 border border-rose-200 rounded-lg"
            >
              {{ chartError }}
            </div>
            <template v-else-if="etfHistory && etfHistory.points.length">
              <CandlestickChart :points="etfHistory.points" height="320px" />
              <p
                v-if="etfHistory.read"
                class="mt-2 text-xs leading-relaxed text-slate-500 bg-slate-50 border border-slate-100 rounded-lg p-2.5"
              >
                {{ etfHistory.read }}
              </p>
            </template>
            <div v-else class="py-10 text-center text-sm text-slate-400">
              <template v-if="etf && etf.listing === '场外'">
                场外联接基金无场内日 K 线行情。其净值与涨跌请见「场外基金」模块（盈米数据源）。
              </template>
              <template v-else>
                该 ETF 暂无历史行情，暂时无法形成判断。
              </template>
            </div>
          </Card>

          <!-- 盘中分时 -->
          <Card
            :title="`盘中分时`"
            :subtitle="intraday ? `${intraday.date} · 昨收 ${intraday.prev_close != null ? intraday.prev_close.toFixed(3) : '—'}` : ''"
          >
            <div v-if="chartLoading" class="py-10 flex flex-col items-center gap-2 text-slate-400">
              <span class="w-5 h-5 border-2 border-slate-300 border-t-slate-500 rounded-full animate-spin" />
              <span class="text-xs">加载分时…</span>
            </div>
            <template v-else-if="intraday && intraday.points.length">
              <IntradayChart :data="intraday" height="280px" />
              <p
                v-if="intraday.read"
                class="mt-2 text-xs leading-relaxed text-slate-500 bg-slate-50 border border-slate-100 rounded-lg p-2.5"
              >
                {{ intraday.read }}
              </p>
            </template>
            <div v-else class="py-10 text-center text-sm text-slate-400">
              盘前或当日分时尚未采集，开盘后每 60 秒自动更新。
            </div>
          </Card>
        </div>

        <!-- 最新信号 -->
        <Card
          v-if="etf.latest_signal"
          title="最新信号"
          :subtitle="`${etf.latest_signal.phase ? phaseText(etf.latest_signal.phase) : '未标注阶段'} · 生成于 ${toBeijing(etf.latest_signal.generated_at)}${signalStaleText}`"
        >
          <div class="flex flex-col sm:flex-row gap-4 items-center">
            <GaugeChart
              :score="etf.latest_signal.score"
              label="综合分"
              height="150px"
              class="shrink-0 w-[180px]"
            />
            <div class="grid grid-cols-2 sm:grid-cols-3 gap-3 text-sm flex-1 w-full">
              <div>
                <div class="text-slate-400 text-xs">置信度</div>
                <div class="tnum font-semibold">{{ fmtConfidence(etf.latest_signal.confidence) }}</div>
              </div>
              <div>
                <div class="text-slate-400 text-xs">市场环境</div>
                <div class="font-semibold">{{ regimeText(etf.latest_signal.market_regime) }}</div>
              </div>
              <div>
                <div class="text-slate-400 text-xs">建议仓位</div>
                <div class="font-semibold text-slate-700">{{ etf.latest_signal.position_text }}</div>
              </div>
            </div>
          </div>
          <div v-if="etf.latest_signal.suggested_action" class="mt-3 text-sm text-slate-600">
            {{ etf.latest_signal.suggested_action }}
          </div>
          <!-- 盘中强度/倾向（C23：live 相位信号带出） -->
          <div v-if="liveStrength" class="mt-3 flex flex-wrap items-center gap-2 text-xs">
            <span class="rounded-full bg-indigo-50 px-2 py-0.5 text-indigo-600">盘中强度 {{ liveStrength.score ?? '—' }}/100</span>
            <span v-if="liveStrength.lean" class="rounded-full bg-slate-100 px-2 py-0.5 text-slate-600">倾向：{{ liveStrength.lean }}</span>
            <span v-if="liveStrength.r1" class="rounded-full bg-emerald-50 px-2 py-0.5 text-emerald-600">R1 补仓看多</span>
            <span v-if="liveStrength.r2" class="rounded-full bg-sky-50 px-2 py-0.5 text-sky-600">R2 超跌抄底</span>
          </div>
          <!-- 数据不足提示 -->
          <div
            v-if="missingRules.length"
            class="mt-3 text-xs text-amber-600 bg-amber-50 border border-amber-200 rounded-md px-3 py-2"
          >
            部分数据缺失（{{ missingRules.join('、') }}），当前为观察期数据，信号置信度已降级。
          </div>
          <!-- 重新评估（C23：盘中即时按需重算） -->
          <div class="mt-3 flex items-center gap-2">
            <button
              type="button"
              :disabled="refreshing"
              class="text-xs px-2.5 py-1 rounded-md border border-slate-200 text-slate-600 hover:bg-slate-50 disabled:opacity-50"
              @click="onRefresh"
            >
              {{ refreshing ? '评估中…' : '重新评估' }}
            </button>
            <span v-if="refreshError" class="text-xs text-rose-500">{{ refreshError }}</span>
            <span v-else class="text-xs text-slate-400">盘中每 5 分钟自动更新</span>
          </div>
        </Card>
        <div v-else class="text-sm text-slate-400 py-4">该 ETF 暂无信号。</div>

        <!-- 盘中意见（主） -->
        <Card class="mt-4" :title="`盘中意见（${intradayOpinions.length}）`">
          <StatePanel
            :loading="false"
            :error="null"
            :empty="intradayOpinions.length === 0"
            empty-text="暂无盘中意见"
          >
            <OpinionList :opinions="intradayOpinions" />
          </StatePanel>
        </Card>

        <!-- 午盘意见（lunch 阶段，C23：午休后生成，可留历史） -->
        <Card class="mt-4" :title="`午盘意见（${lunchOpinions.length}）`">
          <StatePanel
            :loading="false"
            :error="null"
            :empty="lunchOpinions.length === 0"
            empty-text="暂无午盘意见（交易日 11:40 后生成）"
          >
            <OpinionList :opinions="lunchOpinions" />
          </StatePanel>
        </Card>

        <!-- 收盘后复盘（次日建议，独立成区） -->
        <Card class="mt-4" :title="`收盘后复盘（${postCloseOpinions.length}）`">
          <StatePanel
            :loading="false"
            :error="null"
            :empty="postCloseOpinions.length === 0"
            empty-text="暂无收盘后复盘"
          >
            <OpinionList :opinions="postCloseOpinions" />
          </StatePanel>
        </Card>

        <!-- 历史信号 -->
        <Card class="mt-4" :title="`历史信号（${history.length}）`">
          <StatePanel :loading="false" :error="null" :empty="history.length === 0" empty-text="暂无历史信号">
            <SignalTable :signals="history" />
          </StatePanel>
        </Card>
      </template>
      <div v-else class="text-sm text-slate-400 text-center py-10">
        未找到该 ETF（代码：{{ code }}）
      </div>
    </StatePanel>
  </div>
</template>
