<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import * as echarts from 'echarts'
import type { EChartsOption } from 'echarts'

// 轻量 ECharts 封装：挂载初始化、option 变化时重渲染、窗口 resize 自适应、卸载销毁。
const props = withDefaults(
  defineProps<{
    option: EChartsOption
    height?: string
  }>(),
  { height: '260px' },
)

const el = ref<HTMLDivElement | null>(null)
const chart = shallowRef<echarts.ECharts | null>(null)

function render(): void {
  if (!el.value) return
  if (!chart.value) {
    chart.value = echarts.init(el.value)
  }
  // notMerge=true：避免新旧 option 残留系列导致图形错乱
  chart.value.setOption(props.option, true)
}

function resize(): void {
  chart.value?.resize()
}

onMounted(() => {
  render()
  window.addEventListener('resize', resize)
})

watch(
  () => props.option,
  () => render(),
  { deep: true },
)

onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  chart.value?.dispose()
  chart.value = null
})
</script>

<template>
  <div ref="el" :style="{ height: height, width: '100%' }" />
</template>
