<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import Modal from '@/components/ui/Modal.vue'
import BaseChart from '@/components/charts/BaseChart.vue'
import { apiGet } from '@/api/client'
import { changeColor, fmtPct } from '@/lib/format'
import type { EChartsOption } from 'echarts'
import type { UsImpactItem, UsImpactOut } from '@/api/types'

// 美股对A股影响抽屉（#109）：点击首页「美股」条单只指数打开。
// 展示该美股隔夜涨跌 → A股次日（沪深300）反应的近期相关性/β + 近期传导明细 + 对比图。
const props = defineProps<{ code: string | null }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const loading = ref(false)
const error = ref<string | null>(null)
const impact = ref<UsImpactOut | null>(null)

const isOpen = computed(() => props.code !== null)
const title = computed(() => `美股对A股影响 · ${item.value?.name ?? ''}`)

const item = computed<UsImpactItem | null>(() => {
  if (!props.code || !impact.value) return null
  return impact.value.items.find((i) => i.code === props.code) ?? null
})

async function load(): Promise<void> {
  if (!props.code) {
    impact.value = null
    return
  }
  loading.value = true
  error.value = null
  try {
    impact.value = await apiGet<UsImpactOut>('/market/us-impact')
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载失败'
    impact.value = null
  } finally {
    loading.value = false
  }
}

watch(
  () => props.code,
  (c) => {
    if (c) load()
    else impact.value = null
  },
  { immediate: true },
)

function corrStrength(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  const a = Math.abs(v)
  if (a >= 0.5) return '强'
  if (a >= 0.3) return '中'
  if (a >= 0.15) return '弱'
  return '无明显'
}
function corrSign(v: number | null | undefined): string {
  if (v === null || v === undefined) return ''
  return v > 0 ? '正相关' : '负相关'
}

const chartOption = computed<EChartsOption>(() => {
  const pts = item.value?.recent ?? []
  const dates = pts.map((p) => p.us_date.slice(5))
  return {
    grid: { left: 40, right: 14, top: 30, bottom: 24 },
    tooltip: { trigger: 'axis' },
    legend: { data: ['美股%', 'A股次日%'], top: 0, textStyle: { fontSize: 11 } },
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 10 } },
    yAxis: { type: 'value', axisLabel: { formatter: '{value}%', fontSize: 10 } },
    series: [
      {
        name: '美股%',
        type: 'line',
        smooth: true,
        showSymbol: false,
        data: pts.map((p) => p.us_pct),
        itemStyle: { color: '#2563eb' },
        lineStyle: { width: 2 },
      },
      {
        name: 'A股次日%',
        type: 'line',
        smooth: true,
        showSymbol: false,
        data: pts.map((p) => p.ashare_pct),
        itemStyle: { color: '#dc2626' },
        lineStyle: { width: 2 },
      },
    ],
  }
})
</script>

<template>
  <Modal :open="isOpen" :title="title" @close="emit('close')">
    <div v-if="loading" class="py-8 text-center text-sm text-slate-400">加载中…</div>
    <div v-else-if="error" class="py-8 text-center text-sm text-rose-500">{{ error }}</div>

    <div v-else-if="item && !item.available" class="py-6 text-sm text-slate-500">
      <p>{{ item.note || '观察期数据不足，暂无法计算影响。' }}</p>
      <p class="mt-2 text-xs text-slate-400">
        美股日线由每日 16:30 回填累积，约 1–2 周后可自动可用。
      </p>
    </div>

    <div v-else-if="item" class="space-y-4">
      <!-- 当前涨跌 -->
      <div class="flex items-baseline gap-2">
        <span class="text-sm text-slate-500">当前</span>
        <span class="tnum text-lg font-semibold" :class="changeColor(item.current_change_percent)">
          {{ fmtPct(item.current_change_percent) }}
        </span>
        <span class="text-xs text-slate-400">（腾讯财经实时）</span>
      </div>

      <!-- 指标卡 -->
      <div class="grid grid-cols-3 gap-2">
        <div class="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
          <div class="text-xs text-slate-400">近期相关</div>
          <div class="tnum text-sm font-semibold text-slate-700">
            {{ item.correlation_recent === null ? '—' : item.correlation_recent.toFixed(2) }}
          </div>
          <div class="text-[11px] text-slate-400">
            {{ corrStrength(item.correlation_recent) }}{{ corrSign(item.correlation_recent) }}
          </div>
        </div>
        <div class="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
          <div class="text-xs text-slate-400">长期相关</div>
          <div class="tnum text-sm font-semibold text-slate-700">
            {{ item.correlation_long === null ? '—' : item.correlation_long.toFixed(2) }}
          </div>
          <div class="text-[11px] text-slate-400">
            {{ corrStrength(item.correlation_long) }}{{ corrSign(item.correlation_long) }}
          </div>
        </div>
        <div class="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
          <div class="text-xs text-slate-400">β（弹性）</div>
          <div class="tnum text-sm font-semibold text-slate-700">
            {{ item.beta === null ? '—' : item.beta.toFixed(2) }}
          </div>
          <div class="text-[11px] text-slate-400">
            {{ item.beta === null ? '' : item.beta > 0 ? '同向' : '反向' }}
          </div>
        </div>
      </div>

      <p class="text-xs leading-relaxed text-slate-500">
        口径：美股隔夜收盘涨跌 → A股次日（{{ impact?.primary_benchmark_name }}）反应。
        相关性/β 基于{{ item.pair_count }}个跨市场配对日收益（近期窗口≈20、长期窗口≈60 个交易日）。
      </p>

      <!-- 对比图 -->
      <div>
        <div class="mb-1 text-xs font-medium text-slate-500">近期走势对比（美股% vs A股次日%）</div>
        <BaseChart :option="chartOption" height="220px" />
      </div>

      <!-- 近期传导明细 -->
      <div>
        <div class="mb-1 text-xs font-medium text-slate-500">近期传导明细</div>
        <div class="overflow-x-auto">
          <table class="w-full text-xs">
            <thead>
              <tr class="text-slate-400 border-b border-slate-100">
                <th class="text-left font-normal py-1.5">美股日</th>
                <th class="text-right font-normal py-1.5">美股%</th>
                <th class="text-left font-normal py-1.5 pl-3">A股次日</th>
                <th class="text-right font-normal py-1.5">A股次日%</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="p in item.recent" :key="p.us_date" class="border-b border-slate-50">
                <td class="py-1.5 tnum text-slate-500">{{ p.us_date }}</td>
                <td class="py-1.5 text-right tnum" :class="changeColor(p.us_pct)">{{ fmtPct(p.us_pct) }}</td>
                <td class="py-1.5 pl-3 tnum text-slate-500">{{ p.ashare_date }}</td>
                <td class="py-1.5 text-right tnum" :class="changeColor(p.ashare_pct)">{{ fmtPct(p.ashare_pct) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </Modal>
</template>
