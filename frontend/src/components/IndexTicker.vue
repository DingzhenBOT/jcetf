<script setup lang="ts">
import { computed } from 'vue'
import { marketState } from '@/stores/market'
import { changeColor, fmtPct } from '@/lib/format'
import type { IndexSnapshot } from '@/api/types'

// 顶部指数数字带：上证指数（000001）作 hero 突出，其余指数红涨绿跌 + 百分比。
// 点击任意指数 -> emit('open', code) 打开详情抽屉。
const emit = defineEmits<{ (e: 'open', code: string): void }>()

const HERO = '000001'

const sorted = computed<IndexSnapshot[]>(() => {
  const list = [...(marketState.overview?.indices ?? [])]
  // 上证指数固定排首位，其余保持原顺序
  return list.sort((a, b) => (a.code === HERO ? -1 : b.code === HERO ? 1 : 0))
})

const hero = computed<IndexSnapshot | undefined>(() => sorted.value.find((i) => i.code === HERO))
const others = computed<IndexSnapshot[]>(() => sorted.value.filter((i) => i.code !== HERO))

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
    class="rounded-xl border border-slate-200 bg-gradient-to-r from-slate-100 via-slate-50 to-white shadow-sm overflow-hidden"
  >
    <div class="flex items-stretch overflow-x-auto no-scrollbar">
      <!-- hero：上证指数 -->
      <button
        v-if="hero"
        type="button"
        class="group flex-shrink-0 min-w-[200px] px-5 py-3 text-left border-r border-slate-200 hover:bg-white/70 active:scale-[0.99] transition focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-300"
        @click="emit('open', hero.code)"
        :aria-label="`查看 ${hero.name} 详情`"
      >
        <div class="text-xs text-slate-500">{{ hero.name }}</div>
        <div class="flex items-baseline gap-2 mt-0.5">
          <span class="tnum text-2xl font-semibold text-slate-800">{{ fmtClose(hero.close) }}</span>
          <span class="tnum text-sm font-medium" :class="changeColor(hero.change_percent)">
            {{ arrow(hero.change_percent) }} {{ fmtPct(hero.change_percent) }}
          </span>
        </div>
      </button>

      <!-- 其余指数 -->
      <button
        v-for="it in others"
        :key="it.code"
        type="button"
        class="group flex-shrink-0 px-4 py-3 text-left border-r border-slate-100 hover:bg-white/70 active:scale-[0.99] transition focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-300 min-w-[132px]"
        @click="emit('open', it.code)"
        :aria-label="`查看 ${it.name} 详情`"
      >
        <div class="text-xs text-slate-500 truncate">{{ it.name }}</div>
        <div class="tnum text-base font-semibold text-slate-700 mt-0.5">{{ fmtClose(it.close) }}</div>
        <div class="tnum text-xs font-medium" :class="changeColor(it.change_percent)">
          {{ arrow(it.change_percent) }} {{ fmtPct(it.change_percent) }}
        </div>
      </button>

      <div v-if="!hero && others.length === 0" class="px-5 py-4 text-sm text-slate-400">
        暂无指数数据（观察期）
      </div>
    </div>
  </div>
</template>
