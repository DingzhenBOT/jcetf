<script setup lang="ts">
import { computed } from 'vue'
import BaseChart from './BaseChart.vue'
import type { EChartsOption } from 'echarts'
import type { SignalRisk } from '@/api/types'
import { TIER_TEXT, TIER_COLOR } from '@/lib/tier'

const props = defineProps<{ risk: SignalRisk | null }>()

const option = computed<EChartsOption>(() => {
  const counts = props.risk?.counts ?? {}
  const data = Object.entries(counts).map(([k, v]) => ({
    name: TIER_TEXT[k as keyof typeof TIER_TEXT] ?? k,
    value: v,
    itemStyle: { color: TIER_COLOR[k as keyof typeof TIER_COLOR] ?? '#94a3b8' },
  }))
  return {
    tooltip: { trigger: 'item', formatter: '{b}: {c} 支 ({d}%)' },
    legend: { bottom: 0, type: 'scroll', textStyle: { fontSize: 11, color: '#64748b' } },
    series: [
      {
        type: 'pie',
        radius: ['45%', '70%'],
        center: ['50%', '44%'],
        avoidLabelOverlap: true,
        label: { show: false },
        data,
      },
    ],
  }
})
</script>

<template>
  <BaseChart :option="option" height="240px" />
</template>
