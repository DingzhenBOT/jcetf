<script setup lang="ts">
import { computed } from 'vue'
import type { EChartsOption } from 'echarts'
import BaseChart from '@/components/charts/BaseChart.vue'

// 摆锤图：把指数的当日涨跌幅画成「绕 0 点摆动的指针」。
// 右偏（红）= 上涨，左偏（绿）= 下跌；摆幅越大说明当日波动越强。
// 用于首页指数卡，直观替代原来的纯数字。
const props = withDefaults(
  defineProps<{
    changePercent?: number | null
    max?: number // 摆锤满量程（%），超出夹到边缘。默认 4
    height?: string
  }>(),
  { max: 4, height: '104px' },
)

const UP = '#dc2626' // 涨：红
const DOWN = '#16a34a' // 跌：绿

const isUp = computed(() => (props.changePercent ?? 0) >= 0)
const color = computed(() => (isUp.value ? UP : DOWN))
// 指针位置按 max 夹住，但真实值仍由父级文字展示
const clamped = computed(() => {
  const v = props.changePercent ?? 0
  return Math.max(-props.max, Math.min(props.max, v))
})

const option = computed<EChartsOption>(() => ({
  animation: true,
  series: [
    {
      type: 'gauge',
      startAngle: 180,
      endAngle: 0,
      min: -props.max,
      max: props.max,
      center: ['50%', '82%'],
      radius: '108%',
      progress: { show: false },
      // 左半（负，跌）绿，右半（正，涨）红
      axisLine: {
        lineStyle: {
          width: 7,
          color: [
            [0.5, DOWN],
            [1, UP],
          ],
        },
      },
      pointer: {
        length: '58%',
        width: 4,
        itemStyle: { color: color.value },
      },
      anchor: {
        show: true,
        size: 9,
        itemStyle: { color: color.value, borderColor: '#fff', borderWidth: 2 },
      },
      axisTick: { show: false },
      splitLine: { show: true, length: 7, lineStyle: { color: '#cbd5e1', width: 1 } },
      axisLabel: { show: false },
      detail: { show: false },
      data: [{ value: clamped.value }],
    },
  ],
}))
</script>

<template>
  <BaseChart :option="option" :height="height" />
</template>
