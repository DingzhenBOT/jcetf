<script setup lang="ts">
import { computed } from 'vue'
import type { EChartsOption } from 'echarts'
import type { Intraday } from '@/api/types'
import BaseChart from '@/components/charts/BaseChart.vue'
import { fmtInt } from '@/lib/format'

// 盘中分时图（类似同花顺）：价格线（红涨绿跌）+ 均价线 + 昨收基准线 + 底部成交量。
// 价格/均价共享上图，成交量在独立下图；两端点共用时间轴联动。
const props = withDefaults(
  defineProps<{
    data: Intraday | null
    height?: string
  }>(),
  { height: '300px' },
)

const hasData = computed(() => (props.data?.points?.length ?? 0) > 0)
const prevClose = computed(() => props.data?.prev_close ?? null)

const option = computed<EChartsOption>(() => {
  const pts = props.data?.points ?? []
  const times = pts.map((p) => p.time.slice(11, 16)) // HH:MM
  const prices = pts.map((p) => p.price)
  const avgs = pts.map((p) => p.avg)
  const vols = pts.map((p) => p.volume)
  const pc = prevClose.value

  const last = prices.length ? prices[prices.length - 1] : 0
  const up = '#dc2626'
  const down = '#16a34a'
  const color = pc != null && last < pc ? down : up

  const allP = prices.concat(pc != null ? [pc] : [])
  const minP = Math.min(...allP)
  const maxP = Math.max(...allP)
  const pad = (maxP - minP) * 0.1 || 0.01

  return {
    animation: false,
    grid: [
      { left: 8, right: 16, top: 16, height: '56%', containLabel: true },
      { left: 8, right: 16, top: '70%', height: '20%', containLabel: true },
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
            s += `${it.marker}${it.seriesName}：${fmtInt(it.value)}<br/>`
          } else {
            s += `${it.marker}${it.seriesName}：<b>${(it.value as number).toFixed(3)}</b><br/>`
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
        data: times,
        boundaryGap: false,
        axisLabel: { color: '#94a3b8', fontSize: 10, hideOverlap: true },
        axisLine: { lineStyle: { color: '#e2e8f0' } },
        axisTick: { show: false },
      },
      {
        type: 'category',
        data: times,
        boundaryGap: false,
        gridIndex: 1,
        axisLabel: { show: false },
        axisLine: { lineStyle: { color: '#e2e8f0' } },
        axisTick: { show: false },
      },
    ],
    yAxis: [
      {
        type: 'value',
        scale: true,
        min: minP - pad,
        max: maxP + pad,
        axisLabel: { color: '#94a3b8', fontSize: 10 },
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
        name: '价格',
        type: 'line',
        data: prices,
        showSymbol: false,
        lineStyle: { color, width: 1.5 },
        markLine: pc != null
          ? {
              silent: true,
              symbol: 'none',
              lineStyle: { color: '#94a3b8', type: 'dashed', width: 1 },
              data: [{ yAxis: pc }],
              label: { formatter: pc.toFixed(3), color: '#94a3b8', fontSize: 9, position: 'end' },
            }
          : undefined,
      },
      {
        name: '均价',
        type: 'line',
        data: avgs,
        showSymbol: false,
        lineStyle: { color: '#f59e0b', width: 1 },
      },
      {
        name: '成交量',
        type: 'bar',
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: vols.map((v, i) => ({
          value: v,
          itemStyle: { color: prices[i] >= (pc ?? prices[i]) ? '#dc2626' : '#16a34a' },
        })),
      },
    ],
  }
})
</script>

<template>
  <BaseChart :option="option" :height="height" />
</template>
