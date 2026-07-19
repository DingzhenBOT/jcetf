<script setup lang="ts">
import { computed } from 'vue'
import BaseChart from './BaseChart.vue'
import type { EChartsOption } from 'echarts'
import type { IndexSnapshot } from '@/api/types'

const props = defineProps<{ indices: IndexSnapshot[] }>()

// 反转使第一个指数显示在顶部；涨=红、跌=绿
const option = computed<EChartsOption>(() => {
  const items = [...props.indices].reverse()
  return {
    grid: { left: 8, right: 44, top: 8, bottom: 8, containLabel: true },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (p: any) => {
        const it = Array.isArray(p) ? p[0] : p
        const v = it.value as number
        return `${it.name}<br/>涨跌幅：${v > 0 ? '+' : ''}${v}%`
      },
    },
    xAxis: {
      type: 'value',
      axisLabel: { formatter: '{value}%', color: '#94a3b8' },
      splitLine: { lineStyle: { color: '#f1f5f9' } },
    },
    yAxis: {
      type: 'category',
      data: items.map((i) => i.name),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: '#e2e8f0' } },
      axisLabel: { color: '#475569' },
    },
    series: [
      {
        type: 'bar',
        barWidth: '55%',
        data: items.map((i) => {
          const v = i.change_percent ?? 0
          return { value: v, itemStyle: { color: v >= 0 ? '#dc2626' : '#16a34a' } }
        }),
        label: {
          show: true,
          position: 'right',
          fontSize: 11,
          color: '#475569',
          formatter: (p: any) => `${p.value > 0 ? '+' : ''}${p.value}%`,
        },
      },
    ],
  }
})
</script>

<template>
  <BaseChart :option="option" height="180px" />
</template>
