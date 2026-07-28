<script setup lang="ts">
import { computed } from 'vue'
import type { EChartsOption } from 'echarts'
import type { Intraday } from '@/api/types'
import BaseChart from '@/components/charts/BaseChart.vue'
import { fmtInt } from '@/lib/format'

// 盘中分时图（重构 C19-G）：
// - x 轴写死 A 股交易时段（09:30-11:30 / 13:00-15:00），午休留空槽使折线断开；
//   仅填充到「当前已采集到的时点」，未来时段为 null（读到几点画到哪，不累积多日）。
// - y 轴改为「当日涨跌幅百分比」(价格 vs 昨收)，0 轴为昨收基准，红涨绿跌，心电图式波动。
// - 底部成交量与 x 轴严格对齐（共用同一交易时段类目）。
const props = withDefaults(
  defineProps<{
    data: Intraday | null
    height?: string
  }>(),
  { height: '340px' },
)

const hasData = computed(() => (props.data?.points?.length ?? 0) > 0)
const prevClose = computed(() => props.data?.prev_close ?? null)

const up = '#dc2626'
const down = '#16a34a'

// 交易时段类目（含午休空槽，使折线在午休自然断开）
const SESSION_LABELS = (() => {
  const pad = (n: number) => String(n).padStart(2, '0')
  const out: string[] = []
  for (let h = 9; h <= 11; h++) {
    const mStart = h === 9 ? 30 : 0
    const mEnd = h === 11 ? 30 : 59
    for (let m = mStart; m <= mEnd; m++) out.push(`${pad(h)}:${pad(m)}`)
  }
  for (let h = 11; h <= 12; h++) {
    const mStart = h === 11 ? 31 : 0
    const mEnd = 59
    for (let m = mStart; m <= mEnd; m++) out.push(`${pad(h)}:${pad(m)}`)
  }
  for (let h = 13; h <= 15; h++) {
    const mStart = 13
    const mEnd = h === 15 ? 0 : 59
    for (let m = mStart; m <= mEnd; m++) out.push(`${pad(h)}:${pad(m)}`)
  }
  return out
})()
const LABEL_TO_IDX = new Map(SESSION_LABELS.map((l, i) => [l, i]))
// x 轴仅展示整点/半点刻度
const AXIS_TICKS = new Set(['09:30', '10:00', '10:30', '11:00', '11:30', '13:00', '13:30', '14:00', '14:30', '15:00'])

const option = computed<EChartsOption>(() => {
  const pts = props.data?.points ?? []
  const pc = prevClose.value

  // 基准：昨收优先，缺失时退回首点（当日开盘）
  const base = pc ?? (pts.length ? pts[0].price : null)

  // 按交易时段类目对齐：涨跌幅% 与成交量落在各自索引，未来时段为 null
  const changePct: (number | null)[] = new Array(SESSION_LABELS.length).fill(null)
  const vols: (number | null)[] = new Array(SESSION_LABELS.length).fill(null)
  const priceByLabel = new Map<string, number>()
  for (const p of pts) {
    const hhmm = p.time.slice(11, 16)
    const idx = LABEL_TO_IDX.get(hhmm)
    if (idx == null) continue
    const cp = base != null && base !== 0 ? (p.price / base - 1) * 100 : 0
    changePct[idx] = Number(cp.toFixed(2))
    vols[idx] = p.volume
    priceByLabel.set(hhmm, p.price)
  }

  // 线条整体着色：按末点相对基准的方向（红涨绿跌）
  let last = 0
  for (let i = changePct.length - 1; i >= 0; i--) {
    const v = changePct[i]
    if (v != null) {
      last = v
      break
    }
  }
  const lineColor = last >= 0 ? up : down

  return {
    animation: false,
    grid: [
      { left: 8, right: 16, top: 16, height: '58%', containLabel: true },
      { left: 8, right: 16, top: '72%', height: '20%', containLabel: true },
    ],
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      formatter: (p: any) => {
        const arr = Array.isArray(p) ? p : [p]
        const t = arr[0]?.axisValue ?? ''
        let s = `${t}<br/>`
        for (const it of arr) {
          if (it.seriesName === '成交量') {
            s += `${it.marker}${it.seriesName}：${it.value != null ? fmtInt(it.value) : '--'}<br/>`
          } else if (it.value != null) {
            const v = Number(it.value)
            s += `${it.marker}${it.seriesName}：<b>${v >= 0 ? '+' : ''}${v.toFixed(2)}%</b><br/>`
          }
        }
        if (pc != null) s += `昨收：<b>${pc.toFixed(3)}</b>`
        return s
      },
    },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    xAxis: [
      {
        type: 'category',
        data: SESSION_LABELS,
        boundaryGap: false,
        axisLabel: {
          color: '#94a3b8',
          fontSize: 10,
          hideOverlap: true,
          formatter: (v: string) => (AXIS_TICKS.has(v) ? v : ''),
        },
        axisLine: { lineStyle: { color: '#e2e8f0' } },
        axisTick: { show: false },
      },
      {
        type: 'category',
        data: SESSION_LABELS,
        gridIndex: 1,
        boundaryGap: false,
        axisLabel: { show: false },
        axisLine: { lineStyle: { color: '#e2e8f0' } },
        axisTick: { show: false },
      },
    ],
    yAxis: [
      {
        type: 'value',
        scale: true,
        axisLabel: {
          color: '#94a3b8',
          fontSize: 10,
          formatter: (v: number) => `${v > 0 ? '+' : ''}${v.toFixed(0)}%`,
        },
        splitLine: { lineStyle: { color: '#f1f5f9' } },
      },
      {
        type: 'value',
        gridIndex: 1,
        axisLabel: { show: false },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: '涨跌幅',
        type: 'line',
        data: changePct,
        showSymbol: false,
        lineStyle: { color: lineColor, width: 1.5 },
        areaStyle: { color: lineColor, opacity: 0.06 },
        markLine: pc != null
          ? {
              silent: true,
              symbol: 'none',
              lineStyle: { color: '#94a3b8', type: 'dashed', width: 1 },
              data: [{ yAxis: 0 as number }],
              label: { formatter: '昨收', color: '#94a3b8', fontSize: 9, position: 'end' },
            }
          : undefined,
      },
      {
        name: '成交量',
        type: 'bar',
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: vols.map((v, i) => {
          if (v == null) return { value: null }
          const price = priceByLabel.get(SESSION_LABELS[i])
          const c = price != null && pc != null ? (price >= pc ? up : down) : '#94a3b8'
          return { value: v, itemStyle: { color: c } }
        }),
      },
    ],
  }
})
</script>

<template>
  <BaseChart :option="option" :height="height" />
</template>
