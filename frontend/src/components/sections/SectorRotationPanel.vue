<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { getSectorMovement } from '@/api/endpoints'
import type { SectorMovement } from '@/api/types'

// 首页紧凑「题材轮动榜」面板：行业题材涨幅 TOP + 资金流入 TOP（来源：腾讯自选股异动榜）。
// 自带轮询（默认 120s），不依赖总览轮询，避免与信号轮询耦合。
const TOP = 6
const POLL_MS = 120_000

const data = ref<SectorMovement | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
let timer: ReturnType<typeof setInterval> | null = null

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    data.value = await getSectorMovement()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '未知错误'
  } finally {
    loading.value = false
  }
}

function pct(v: any): string {
  const n = Number(v)
  if (!isFinite(n)) return '--'
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`
}
function cls(v: any): string {
  const n = Number(v)
  if (!isFinite(n) || n === 0) return 'text-slate-500'
  return n > 0 ? 'text-rose-600' : 'text-emerald-600'
}
function inflow(v: any): string {
  const n = Number(v)
  if (!isFinite(n) || n === 0) return '--'
  const abs = Math.abs(n)
  const unit = abs >= 1e8 ? `${(n / 1e8).toFixed(2)}亿` : `${(n / 1e4).toFixed(0)}万`
  return unit
}

const topIndustry = computed(() =>
  [...(data.value?.industry ?? [])].sort((a, b) => Number(b.changePct) - Number(a.changePct)).slice(0, TOP),
)
const topFlow = computed(() =>
  [...(data.value?.fund_flow ?? [])]
    .filter((s) => Number(s.mainNetInflow) > 0)
    .sort((a, b) => Number(b.mainNetInflow) - Number(a.mainNetInflow))
    .slice(0, TOP),
)
const degraded = computed(() => data.value != null && data.value.available === false)

onMounted(() => {
  load()
  timer = setInterval(load, POLL_MS)
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<template>
  <div class="space-y-3">
    <p v-if="degraded" class="text-xs text-amber-600 bg-amber-50 border border-amber-200 rounded-md px-2 py-1">
      异动榜数据源暂不可用（npx westock-data 未运行或网络受限），已降级显示。
    </p>
    <p v-else-if="error" class="text-xs text-rose-500">{{ error }}</p>

    <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
      <!-- 行业题材涨幅 TOP -->
      <div>
        <div class="text-xs font-medium text-slate-400 mb-1.5">行业题材涨幅 TOP{{ TOP }}</div>
        <ul class="divide-y divide-slate-50">
          <li v-for="s in topIndustry" :key="s.name" class="flex items-center justify-between py-1 text-sm">
            <span class="text-slate-700 truncate mr-2">{{ s.name }}</span>
            <span class="tnum font-medium shrink-0" :class="cls(s.changePct)">{{ pct(s.changePct) }}</span>
          </li>
          <li v-if="!topIndustry.length" class="py-2 text-center text-xs text-slate-400">暂无数据</li>
        </ul>
      </div>

      <!-- 资金流入 TOP -->
      <div>
        <div class="text-xs font-medium text-slate-400 mb-1.5">主力资金流入 TOP{{ TOP }}</div>
        <ul class="divide-y divide-slate-50">
          <li v-for="s in topFlow" :key="s.name" class="flex items-center justify-between py-1 text-sm">
            <span class="text-slate-700 truncate mr-2">{{ s.name }}</span>
            <span class="tnum font-medium shrink-0" :class="cls(s.mainNetInflow)">{{ inflow(s.mainNetInflow) }}</span>
          </li>
          <li v-if="!topFlow.length" class="py-2 text-center text-xs text-slate-400">暂无数据</li>
        </ul>
      </div>
    </div>
  </div>
</template>
