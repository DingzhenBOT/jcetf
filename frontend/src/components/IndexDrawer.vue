<script setup lang="ts">
import { computed, ref, watch, onMounted, onUnmounted, nextTick } from 'vue'
import BaseChart from '@/components/charts/BaseChart.vue'
import { getEtfs, getIndexHistory } from '@/api/endpoints'
import { changeColor, fmtPct, fmtAmountYi, fmtInt } from '@/lib/format'
import type { EChartsOption } from 'echarts'
import type { EtfListItem, IndexHistory } from '@/api/types'

const props = defineProps<{ code: string | null }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const loading = ref(false)
const error = ref<string | null>(null)
const data = ref<IndexHistory | null>(null)
const etfs = ref<EtfListItem[]>([])
const closeBtn = ref<HTMLButtonElement | null>(null)

const isOpen = computed(() => props.code !== null)

const relatedEtfs = computed<EtfListItem[]>(() =>
  props.code ? etfs.value.filter((e) => e.related_index_code === props.code) : [],
)

const latest = computed(() => {
  const pts = data.value?.points ?? []
  return pts.length ? pts[pts.length - 1] : null
})

const trendUp = computed(() => {
  const pts = data.value?.points ?? []
  if (pts.length < 2) return true
  return pts[pts.length - 1].close >= pts[0].close
})

const lineOption = computed<EChartsOption>(() => {
  const pts = data.value?.points ?? []
  const dates = pts.map((p) => p.date.slice(5)) // MM-DD
  const closes = pts.map((p) => p.close)
  const color = trendUp.value ? '#dc2626' : '#16a34a'
  return {
    grid: { left: 8, right: 16, top: 16, bottom: 8, containLabel: true },
    tooltip: {
      trigger: 'axis',
      formatter: (p: any) => {
        const it = Array.isArray(p) ? p[0] : p
        return `${it.name}<br/>收盘：<b>${it.value.toFixed(2)}</b>`
      },
    },
    xAxis: {
      type: 'category',
      data: dates,
      boundaryGap: false,
      axisLabel: { color: '#94a3b8', fontSize: 10, hideOverlap: true },
      axisLine: { lineStyle: { color: '#e2e8f0' } },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      scale: true,
      axisLabel: { color: '#94a3b8', fontSize: 10 },
      splitLine: { lineStyle: { color: '#f1f5f9' } },
    },
    series: [
      {
        type: 'line',
        data: closes,
        smooth: true,
        showSymbol: false,
        lineStyle: { color, width: 2 },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: color + '33' },
              { offset: 1, color: color + '00' },
            ],
          },
        },
      },
    ],
  }
})

const volumeOption = computed<EChartsOption>(() => {
  const pts = data.value?.points ?? []
  const dates = pts.map((p) => p.date.slice(5))
  return {
    grid: { left: 8, right: 16, top: 8, bottom: 8, containLabel: true },
    tooltip: {
      trigger: 'axis',
      formatter: (p: any) => {
        const it = Array.isArray(p) ? p[0] : p
        return `${it.name}<br/>成交量：${fmtInt(it.value)} 手`
      },
    },
    xAxis: {
      type: 'category',
      data: dates,
      axisLabel: { show: false },
      axisLine: { lineStyle: { color: '#e2e8f0' } },
      axisTick: { show: false },
    },
    yAxis: { type: 'value', axisLabel: { show: false }, splitLine: { show: false } },
    series: [
      {
        type: 'bar',
        barWidth: '60%',
        data: pts.map((p) => ({
          value: p.volume,
          itemStyle: { color: (p.change_percent ?? 0) >= 0 ? '#dc2626' : '#16a34a' },
        })),
      },
    ],
  }
})

async function load(): Promise<void> {
  if (!props.code) {
    data.value = null
    etfs.value = []
    error.value = null
    return
  }
  loading.value = true
  error.value = null
  try {
    const [hist, list] = await Promise.all([getIndexHistory(props.code), getEtfs()])
    data.value = hist
    etfs.value = list
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    loading.value = false
  }
}

function onKey(e: KeyboardEvent): void {
  if (e.key === 'Escape' && isOpen.value) emit('close')
}

watch(
  () => props.code,
  (c) => {
    if (c) {
      void load()
      void nextTick(() => closeBtn.value?.focus())
    }
  },
)

onMounted(() => window.addEventListener('keydown', onKey))
onUnmounted(() => window.removeEventListener('keydown', onKey))

function fmtClose(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '--'
  return v.toFixed(2)
}
</script>

<template>
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="isOpen"
        class="fixed inset-0 z-40 bg-slate-900/40 backdrop-blur-sm"
        @click="emit('close')"
        aria-hidden="true"
      />
    </Transition>
    <Transition name="slide">
      <aside
        v-if="isOpen"
        class="fixed z-50 inset-x-0 bottom-0 max-h-[86dvh] flex flex-col rounded-t-2xl bg-white border-t border-slate-200 shadow-2xl
               sm:inset-y-0 sm:left-auto sm:right-0 sm:w-[460px] sm:max-w-[92vw] sm:max-h-none sm:rounded-none sm:rounded-l-2xl sm:border-t-0 sm:border-l"
        role="dialog"
        aria-modal="true"
        :aria-label="data ? `${data.name} 详情` : '指数详情'"
      >
        <!-- 头部 -->
        <header class="flex items-center justify-between gap-3 px-5 py-4 border-b border-slate-100 shrink-0">
          <div v-if="data" class="min-w-0">
            <div class="flex items-baseline gap-2">
              <h2 class="text-lg font-semibold text-slate-800 truncate">{{ data.name }}</h2>
              <span class="text-xs text-slate-400 tnum">{{ data.code }}</span>
            </div>
            <div class="flex items-baseline gap-2 mt-1">
              <span class="tnum text-xl font-semibold text-slate-800">{{ fmtClose(latest?.close) }}</span>
              <span
                v-if="latest"
                class="tnum text-sm font-medium"
                :class="changeColor(latest.change_percent)"
              >
                {{ (latest.change_percent ?? 0) >= 0 ? '▲' : '▼' }} {{ fmtPct(latest.change_percent) }}
              </span>
            </div>
          </div>
          <div v-else class="text-base font-semibold text-slate-700">指数详情</div>
          <button
            ref="closeBtn"
            type="button"
            class="shrink-0 w-9 h-9 grid place-items-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-slate-600 transition focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-300"
            @click="emit('close')"
            aria-label="关闭"
          >
            ✕
          </button>
        </header>

        <!-- 内容 -->
        <div class="flex-1 overflow-y-auto px-5 py-4 space-y-5">
          <!-- 加载 -->
          <div v-if="loading" class="py-16 flex flex-col items-center gap-3 text-slate-400">
            <span class="w-6 h-6 border-2 border-slate-300 border-t-slate-500 rounded-full animate-spin" />
            <span class="text-sm">加载中…</span>
          </div>

          <!-- 错误 -->
          <div
            v-else-if="error"
            class="py-12 text-center text-sm text-rose-600 bg-rose-50 border border-rose-200 rounded-lg"
          >
            {{ error }}
            <div class="mt-3">
              <button
                type="button"
                class="px-3 py-1.5 rounded-md border border-rose-300 text-rose-700 hover:bg-rose-100 transition text-xs"
                @click="load()"
              >
                重试
              </button>
            </div>
          </div>

          <!-- 空数据 -->
          <div
            v-else-if="data && data.points.length === 0"
            class="py-12 text-center text-sm text-slate-400"
          >
            该指数暂无历史行情，暂时无法形成判断。
          </div>

          <!-- 正常 -->
          <template v-else-if="data">
            <!-- 标签 -->
            <div v-if="data.signals.length" class="flex flex-wrap gap-1.5">
              <span
                v-for="s in data.signals"
                :key="s"
                class="text-xs px-2 py-0.5 rounded-full bg-slate-100 text-slate-600"
              >{{ s }}</span>
            </div>

            <!-- 折线图 -->
            <section>
              <div class="flex items-center justify-between mb-1">
                <h3 class="text-xs font-medium text-slate-500">收盘价走势</h3>
                <span class="text-xs text-slate-400">近 {{ data.points.length }} 个交易日</span>
              </div>
              <BaseChart :option="lineOption" height="200px" />
            </section>

            <!-- 买卖量 -->
            <section>
              <div class="flex items-center justify-between mb-1">
                <h3 class="text-xs font-medium text-slate-500">成交量（买卖量）</h3>
                <span v-if="latest" class="text-xs text-slate-400 tnum">
                  最新成交额 {{ fmtAmountYi(latest.amount) }}
                </span>
              </div>
              <BaseChart :option="volumeOption" height="120px" />
            </section>

            <!-- 推荐理由（人话） -->
            <section>
              <h3 class="text-xs font-medium text-slate-500 mb-1.5">是否值得参与 · 简评</h3>
              <p class="text-sm leading-relaxed text-slate-700 bg-slate-50 border border-slate-100 rounded-lg p-3">
                {{ data.read }}
              </p>
            </section>

            <!-- 相关 ETF（次级，非主推荐） -->
            <section v-if="relatedEtfs.length">
              <h3 class="text-xs font-medium text-slate-500 mb-1.5">跟踪该指数的 ETF</h3>
              <div class="flex flex-wrap gap-2">
                <router-link
                  v-for="e in relatedEtfs"
                  :key="e.etf_code"
                  :to="`/etfs/${e.etf_code}`"
                  class="text-xs px-2.5 py-1 rounded-md border border-slate-200 text-slate-600 hover:bg-slate-50 hover:text-slate-800 transition"
                  @click="emit('close')"
                >
                  {{ e.etf_name ?? e.etf_code }}
                  <span class="text-slate-400 ml-1">{{ e.etf_code }}</span>
                </router-link>
              </div>
            </section>
          </template>
        </div>
      </aside>
    </Transition>
  </Teleport>
</template>

<style scoped>
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
.slide-enter-active,
.slide-leave-active {
  transition: transform 0.25s ease;
}
.slide-enter-from,
.slide-leave-to {
  transform: translateY(100%);
}
@media (min-width: 640px) {
  .slide-enter-from,
  .slide-leave-to {
    transform: translateX(100%);
  }
}
</style>
