<script setup lang="ts">
import { computed } from 'vue'
import { marketState } from '@/stores/market'
import { changeColor, fmtPct } from '@/lib/format'
import type { IndexSnapshot } from '@/api/types'
import PendulumChart from '@/components/charts/PendulumChart.vue'

// 首页「美股大盘」条：道琼斯/纳斯达克/标普500（腾讯财经实时源，CVM 不封 IP）。
// 点击单只指数 → 打开「美股对A股影响」抽屉（#109）。
const list = computed<IndexSnapshot[]>(() => marketState.overview?.us_indices ?? [])

const emit = defineEmits<{ (e: 'select', code: string): void }>()

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
      <button
        v-for="it in list"
        :key="it.code"
        type="button"
        class="flex-shrink-0 w-[140px] px-4 py-3 border-r border-slate-100 text-left hover:bg-slate-50 active:scale-[0.98] transition cursor-pointer"
        :title="`查看 ${it.name} 对A股的影响`"
        @click="emit('select', it.code)"
      >
        <div class="text-xs text-slate-500 truncate">{{ it.name }}</div>
        <div class="tnum text-base font-semibold text-slate-700 mt-0.5">{{ fmtClose(it.close) }}</div>
        <div class="tnum text-xs font-medium" :class="changeColor(it.change_percent)">
          {{ arrow(it.change_percent) }} {{ fmtPct(it.change_percent) }}
        </div>
        <PendulumChart :change-percent="it.change_percent" :max="4" height="56px" />
      </button>

      <div v-if="list.length === 0" class="px-5 py-4 text-sm text-slate-400">
        美股指数暂不可用（观察期）
      </div>
    </div>
  </div>
</template>
