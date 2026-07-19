<script setup lang="ts">
import { computed } from 'vue'
import BaseChart from './BaseChart.vue'
import type { EChartsOption } from 'echarts'
import type { Breadth } from '@/api/types'

// A股惯例：上涨=红、下跌=绿、平盘=灰
const props = defineProps<{ breadth: Breadth | null }>()

const option = computed<EChartsOption>(() => {
  const b = props.breadth
  const data = [
    { name: '上涨', value: b?.total_rise ?? 0, itemStyle: { color: '#dc2626' } },
    { name: '下跌', value: b?.total_fall ?? 0, itemStyle: { color: '#16a34a' } },
    { name: '平盘', value: b?.total_flat ?? 0, itemStyle: { color: '#94a3b8' } },
  ]
  return {
    tooltip: { trigger: 'item', formatter: '{b}: {c} 家 ({d}%)' },
    legend: { bottom: 0, textStyle: { fontSize: 11, color: '#64748b' } },
    series: [
      {
        type: 'pie',
        radius: ['45%', '70%'],
        center: ['50%', '44%'],
        label: { show: false },
        data,
      },
    ],
  }
})
</script>

<template>
  <BaseChart :option="option" height="220px" />
</template>
