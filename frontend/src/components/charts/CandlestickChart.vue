<script setup lang="ts">
import { computed } from 'vue'
import type { EChartsOption } from 'echarts'
import type { IndexHistoryPoint } from '@/api/types'
import BaseChart from '@/components/charts/BaseChart.vue'

// 同花顺式日 K 线（开/高/低/收）+ 成交量，支持横向缩放，红涨绿跌（A股惯例）。
// ETF 与指数共用。
const props = withDefaults(
  defineProps<{
    points: IndexHistoryPoint[]
    height?: string
  }>(),
  { height: '320px' },
)

const UP = '#dc2626' // 涨：红
const DOWN = '#16a34a' // 跌：绿

// ECharts 蜡烛数据：[open, close, low, high]
const candleData = computed(() =>
  props.points.map((p) => [
    p.open ?? p.close,
    p.close,
    p.low ?? p.close,
    p.high ?? p.close,
  ]),
)
const dates = computed(() => props.points.map((x) => x.date.slice(5))) // MM-DD
const volData = computed(() =>
  props.points.map((p) => ({
    value: p.volume,
    itemStyle: { color: (p.change_percent ?? 0) >= 0 ? UP : DOWN },
  })),
)

const option = computed<EChartsOption>(() => ({
  animation: false,
  axisPointer: { link: [{ xAxisIndex: 'all' }], label: { backgroundColor: '#475569' } },
  tooltip: {
    trigger: 'axis',
    axisPointer: { type: 'cross' },
    formatter: (pp: any) => {
      const arr = Array.isArray(pp) ? pp : [pp]
      const k = arr.find((x: any) => x.seriesType === 'candlestick')
      if (!k) return ''
      const [o, c, l, h] = k.data
      const chg = props.points[k.dataIndex]?.change_percent
      const sign = (chg ?? 0) >= 0 ? '+' : ''
      return (
        `${k.axisValue}<br/>` +
        `开：<b>${Number(o).toFixed(3)}</b>　收：<b>${Number(c).toFixed(3)}</b><br/>` +
        `高：<b>${Number(h).toFixed(3)}</b>　低：<b>${Number(l).toFixed(3)}</b>` +
        (chg != null ? `<br/>涨跌幅：<b>${sign}${chg.toFixed(2)}%</b>` : '')
      )
    },
  },
  grid: [
    { left: 8, right: 16, top: 12, height: '62%', containLabel: true },
    { left: 8, right: 16, top: '74%', height: '18%', containLabel: true },
  ],
  xAxis: [
    {
      type: 'category',
      data: dates.value,
      boundaryGap: true,
      axisLabel: { color: '#94a3b8', fontSize: 10, hideOverlap: true },
      axisLine: { lineStyle: { color: '#e2e8f0' } },
      axisTick: { show: false },
      splitLine: { show: false },
    },
    {
      type: 'category',
      gridIndex: 1,
      data: dates.value,
      boundaryGap: true,
      axisLabel: { show: false },
      axisLine: { lineStyle: { color: '#e2e8f0' } },
      axisTick: { show: false },
      splitLine: { show: false },
    },
  ],
  yAxis: [
    {
      scale: true,
      axisLabel: { color: '#94a3b8', fontSize: 10 },
      splitLine: { lineStyle: { color: '#f1f5f9' } },
    },
    {
      gridIndex: 1,
      scale: true,
      axisLabel: { show: false },
      splitLine: { show: false },
    },
  ],
  dataZoom: [
    { type: 'inside', xAxisIndex: [0, 1], start: 60, end: 100 },
    {
      type: 'slider',
      xAxisIndex: [0, 1],
      bottom: 6,
      height: 14,
      start: 60,
      end: 100,
      borderColor: '#e2e8f0',
      fillerColor: 'rgba(14,165,233,0.12)',
      handleStyle: { color: '#0ea5e9' },
      textStyle: { color: '#94a3b8', fontSize: 10 },
    },
  ],
  series: [
    {
      type: 'candlestick',
      data: candleData.value,
      itemStyle: {
        color: UP,
        color0: DOWN,
        borderColor: UP,
        borderColor0: DOWN,
      },
    },
    {
      type: 'bar',
      xAxisIndex: 1,
      yAxisIndex: 1,
      data: volData.value,
      barWidth: '60%',
    },
  ],
}))
</script>

<template>
  <BaseChart :option="option" :height="height" />
</template>
