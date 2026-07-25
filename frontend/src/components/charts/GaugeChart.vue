<script setup lang="ts">
import { computed } from 'vue'
import type { EChartsOption } from 'echarts'
import BaseChart from '@/components/charts/BaseChart.vue'

// 综合分仪表盘：把信号的 0–100 综合分画成半圆仪表。
// 分档着色：偏低（蓝）/ 中等（琥珀）/ 偏高（绿），指针与读数同色。
// 用于 ETF 详情页「综合分」区，直观替代原来的纯数字方块。
const props = withDefaults(
  defineProps<{
    score?: number | null
    label?: string
    height?: string
  }>(),
  { score: null, label: '综合分', height: '170px' },
)

type Zone = { text: string; color: string }
function zoneOf(v: number): Zone {
  if (v >= 70) return { text: '偏高', color: '#10b981' }
  if (v >= 40) return { text: '中等', color: '#f59e0b' }
  return { text: '偏低', color: '#60a5fa' }
}

const has = computed(() => props.score != null && !Number.isNaN(props.score as number))
const z = computed<Zone>(() => (has.value ? zoneOf(props.score as number) : { text: '—', color: '#94a3b8' }))

const option = computed<EChartsOption>(() => ({
  animation: true,
  series: [
    {
      type: 'gauge',
      startAngle: 200,
      endAngle: -20,
      min: 0,
      max: 100,
      center: ['50%', '62%'],
      radius: '92%',
      progress: { show: false },
      axisLine: {
        lineStyle: {
          width: 12,
          color: [
            [0.4, '#60a5fa'],
            [0.7, '#f59e0b'],
            [1, '#10b981'],
          ],
        },
      },
      pointer: {
        length: '62%',
        width: 5,
        itemStyle: { color: z.value.color },
      },
      anchor: {
        show: true,
        size: 12,
        itemStyle: { color: z.value.color, borderColor: '#fff', borderWidth: 2 },
      },
      axisTick: { show: false },
      splitLine: { show: true, length: 10, lineStyle: { color: '#e2e8f0', width: 2 } },
      axisLabel: { show: true, distance: -2, color: '#94a3b8', fontSize: 9, formatter: (v: number) => (v % 50 === 0 ? String(v) : '') },
      detail: {
        show: true,
        offsetCenter: [0, '34%'],
        formatter: () => (has.value ? `${(props.score as number).toFixed(0)}` : '--'),
        color: z.value.color,
        fontSize: 30,
        fontWeight: 'bold',
      },
      title: { show: false },
      data: [{ value: has.value ? (props.score as number) : 0 }],
    },
  ],
}))
</script>

<template>
  <div class="flex flex-col items-center">
    <BaseChart :option="option" :height="height" />
    <div class="-mt-4 text-xs text-slate-400">
      {{ label }} · <span :style="{ color: z.color }">{{ z.text }}</span>
    </div>
  </div>
</template>
