<script setup lang="ts">
import { computed } from 'vue'
import type { EChartsOption } from 'echarts'
import type { IndexHistoryPoint } from '@/api/types'
import BaseChart from '@/components/charts/BaseChart.vue'

// 收盘价走势 + 成交量（红涨绿跌）。ETF 与指数共用，避免重复实现。
const props = withDefaults(
  defineProps<{
    points: IndexHistoryPoint[]
    height?: string
  }>(),
  { height: '200px' },
)

const trendUp = computed(() => {
  const p = props.points
  if (p.length < 2) return true
  return p[p.length - 1].close >= p[0].close
})

const lineOption = computed<EChartsOption>(() => {
  const p = props.points
  const dates = p.map((x) => x.date.slice(5)) // MM-DD
  const closes = p.map((x) => x.close)
  const color = trendUp.value ? '#dc2626' : '#16a34a'
  return {
    grid: { left: 8, right: 16, top: 16, bottom: 8, containLabel: true },
    tooltip: {
      trigger: 'axis',
      formatter: (pp: any) => {
        const it = Array.isArray(pp) ? pp[0] : pp
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
  const p = props.points
  const dates = p.map((x) => x.date.slice(5))
  return {
    grid: { left: 8, right: 16, top: 8, bottom: 8, containLabel: true },
    tooltip: {
      trigger: 'axis',
      formatter: (pp: any) => {
        const it = Array.isArray(pp) ? pp[0] : pp
        return `${it.name}<br/>成交量：${it.value.toLocaleString('zh-CN')} 手`
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
        data: p.map((x) => ({
          value: x.volume,
          itemStyle: { color: (x.change_percent ?? 0) >= 0 ? '#dc2626' : '#16a34a' },
        })),
      },
    ],
  }
})
</script>

<template>
  <div class="space-y-2">
    <BaseChart :option="lineOption" :height="height" />
    <BaseChart :option="volumeOption" height="100px" />
  </div>
</template>
