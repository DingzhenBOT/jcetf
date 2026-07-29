<script setup lang="ts">
import { computed } from 'vue'
import type { EChartsOption } from 'echarts'
import type { Intraday } from '@/api/types'
import BaseChart from '@/components/charts/BaseChart.vue'
import { fmtInt } from '@/lib/format'

// 盘中分时图（C19-I v2，同花顺风格）：
// - x 轴连续：09:30-11:30 直接接 13:00-15:00，午休不留空槽（折线不断开，11:30 后直接连下午）。
// - y 轴为「当日涨跌幅%」，0% 固定在中线（对称 min/max），红涨绿跌语义由成交量柱体现。
// - 价格线白色（#fff），均价线黄色（#f5c518，累计成交额/累计成交量 VWAP），共享同一 y 轴。
// - 底部成交量与价格共享同一 x 轴；净买入（本分钟价 > 上分钟价）红、净卖出绿、持平灰。
//   暗色面板以保证白线/黄线可见（同花顺分时图观感）。
const props = withDefaults(
  defineProps<{
    data: Intraday | null
    height?: string
  }>(),
  { height: '340px' },
)

const WHITE = '#ffffff'
const YELLOW = '#f5c518'
const RED = '#ef4444'
const GREEN = '#22c55e'
const FLAT = '#64748b'
const GRID_BG = '#0d1117'

const hasData = computed(() => (props.data?.points?.length ?? 0) > 0)
const prevClose = computed(() => props.data?.prev_close ?? null)

// 连续交易时段类目（无午休空槽）：上午 09:30-11:30 + 下午 13:00-15:00
const SESSION_LABELS = (() => {
  const pad = (n: number) => String(n).padStart(2, '0')
  const out: string[] = []
  for (let h = 9; h <= 11; h++) {
    const mStart = h === 9 ? 30 : 0
    for (let m = mStart; m <= 59; m++) out.push(`${pad(h)}:${pad(m)}`)
  }
  for (let h = 13; h <= 15; h++) {
    const mEnd = h === 15 ? 0 : 59
    for (let m = 0; m <= mEnd; m++) out.push(`${pad(h)}:${pad(m)}`)
  }
  return out
})()
const LABEL_TO_IDX = new Map(SESSION_LABELS.map((l, i) => [l, i]))
const AXIS_TICKS = new Set([
  '09:30', '10:00', '10:30', '11:00', '11:30', '13:00', '13:30', '14:00', '14:30', '15:00',
])

const option = computed<EChartsOption>(() => {
  const pts = props.data?.points ?? []
  const pc = prevClose.value

  const changePct: (number | null)[] = new Array(SESSION_LABELS.length).fill(null)
  const avgPct: (number | null)[] = new Array(SESSION_LABELS.length).fill(null)
  const vols: (number | null)[] = new Array(SESSION_LABELS.length).fill(null)
  const priceByLabel = new Map<string, number>()

  for (const p of pts) {
    const hhmm = p.time.slice(11, 16)
    const idx = LABEL_TO_IDX.get(hhmm)
    if (idx == null) continue
    const base = pc ?? p.price
    if (pc != null && pc !== 0) {
      changePct[idx] = Number(((p.price / pc - 1) * 100).toFixed(2))
      if (p.avg != null && p.avg !== 0) {
        avgPct[idx] = Number(((p.avg / pc - 1) * 100).toFixed(2))
      }
    } else {
      changePct[idx] = 0
    }
    vols[idx] = p.volume
    priceByLabel.set(hhmm, p.price)
  }

  // y 轴对称：0% 居中
  const allVals = [...changePct, ...avgPct].filter((v): v is number => v != null)
  const peak = allVals.length ? Math.max(...allVals.map((v) => Math.abs(v))) : 0
  const m = Math.max(0.5, peak * 1.15)

  // 成交量着色：本分钟价 vs 上一分钟价（首分钟 vs 昨收）
  const volItems = vols.map((v, i) => {
    if (v == null) return { value: null }
    const label = SESSION_LABELS[i]
    const price = priceByLabel.get(label)
    const ref = prevPriceByLabel(priceByLabel, label)
    let color = FLAT
    if (price != null && ref != null) color = price > ref ? RED : price < ref ? GREEN : FLAT
    return { value: v, itemStyle: { color } }
  })

  return {
    animation: false,
    backgroundColor: GRID_BG,
    grid: [
      { left: 8, right: 16, top: 16, height: '60%', containLabel: true, backgroundColor: GRID_BG },
      { left: 8, right: 16, top: '74%', height: '18%', containLabel: true, backgroundColor: GRID_BG },
    ],
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      backgroundColor: 'rgba(15,23,42,0.92)',
      borderColor: '#334155',
      textStyle: { color: '#e2e8f0', fontSize: 12 },
      formatter: (p: any) => {
        const arr = Array.isArray(p) ? p : [p]
        const t = arr[0]?.axisValue ?? ''
        let s = `<b>${t}</b><br/>`
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
        axisLine: { lineStyle: { color: '#334155' } },
        axisTick: { show: false },
      },
      {
        type: 'category',
        data: SESSION_LABELS,
        gridIndex: 1,
        boundaryGap: false,
        axisLabel: { show: false },
        axisLine: { lineStyle: { color: '#334155' } },
        axisTick: { show: false },
      },
    ],
    yAxis: [
      {
        type: 'value',
        min: -m,
        max: m,
        axisLabel: {
          color: '#94a3b8',
          fontSize: 10,
          formatter: (v: number) => `${v > 0 ? '+' : ''}${v.toFixed(0)}%`,
        },
        splitLine: { lineStyle: { color: '#1e293b' } },
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
        showSymbol: false,
        connectNulls: true,
        data: changePct,
        lineStyle: { color: WHITE, width: 1.5 },
        areaStyle: { color: WHITE, opacity: 0.06 },
        markLine: pc != null
          ? {
              silent: true,
              symbol: 'none',
              lineStyle: { color: '#94a3b8', type: 'dashed', width: 1 },
              data: [{ yAxis: 0 }],
              label: { formatter: '0%', color: '#94a3b8', fontSize: 9, position: 'end' },
            }
          : undefined,
      },
      {
        name: '均价',
        type: 'line',
        showSymbol: false,
        connectNulls: true,
        data: avgPct,
        lineStyle: { color: YELLOW, width: 1.2 },
        z: 3,
      },
      {
        name: '成交量',
        type: 'bar',
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: volItems,
      },
    ],
  }
})

// 取某标签对应「上一分钟」价格（用于成交量净买/净卖着色）；首分钟回退昨收。
function prevPriceByLabel(map: Map<string, number>, label: string): number | null {
  const [h, m] = label.split(':').map(Number)
  const prev = m - 1
  const prevLabel = prev >= 0 ? `${String(h).padStart(2, '0')}:${String(prev).padStart(2, '0')}` : null
  if (prevLabel && map.has(prevLabel)) return map.get(prevLabel) as number
  // 跨上午/下午边界（11:30 -> 13:00）无上一分钟，回退同段首或昨收
  if (h === 13 && map.has('11:30')) return map.get('11:30') as number
  return prevClose.value
}
</script>

<template>
  <BaseChart :option="option" :height="height" />
</template>
