<script setup lang="ts">
import { computed } from 'vue'
import { marketState } from '@/stores/market'
import { changeColor, fmtPct } from '@/lib/format'
import type { IndexSnapshot } from '@/api/types'
import PendulumChart from '@/components/charts/PendulumChart.vue'

// 首页「美股大盘」条：道琼斯/纳斯达克/标普500（腾讯财经实时源，CVM 不封 IP）。
// 展示型：不打开 A股详情抽屉（美股无本地历史）；红涨绿跌 + 摆锤图。
const list = computed<IndexSnapshot[]>(() => marketState.overview?.us_indices ?? [])

function arrow(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v) || v === 0) return '—'
  return v > 0 ? '▲' : '▼'
}

function fmtClose(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '--'
  return v.toFixed(2)
}
</script>

<template>
  <div
    class="rounded-xl border border-slate-200 bg-gradient-to-r from-slate-50 via-white to-slate-50 shadow-sm overflow-hidden"
  >
    <div class="flex items-stretch overflow-x-auto no-scrollbar">
      <div
        class="flex-shrink-0 px-4 py-3 flex items-center text-xs font-medium text-slate-400 border-r border-slate-200 bg-white/60"
      >
        美股
      </div>
      <div
        v-for="it in list"
        :key="it.code"
        class="flex-shrink-0 w-[140px] px-4 py-3 border-r border-slate-100"
      >
        <div class="text-xs text-slate-500 truncate">{{ it.name }}</div>
        <div class="tnum text-base font-semibold text-slate-700 mt-0.5">{{ fmtClose(it.close) }}</div>
        <div class="tnum text-xs font-medium" :class="changeColor(it.change_percent)">
          {{ arrow(it.change_percent) }} {{ fmtPct(it.change_percent) }}
        </div>
        <PendulumChart :change-percent="it.change_percent" :max="4" height="56px" />
      </div>

      <div v-if="list.length === 0" class="px-5 py-4 text-sm text-slate-400">
        美股指数暂不可用（观察期）
      </div>
    </div>
  </div>
</template>
