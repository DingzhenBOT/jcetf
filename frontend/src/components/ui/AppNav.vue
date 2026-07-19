<script setup lang="ts">
import { computed } from 'vue'
import { marketState } from '@/stores/market'
import { toRelative } from '@/lib/time'
import { riskLevelBadge } from '@/lib/tier'

const links = [
  { to: '/', label: '总览' },
  { to: '/sectors', label: '板块' },
  { to: '/etfs', label: 'ETF 列表' },
  { to: '/portfolio', label: '持仓' },
  { to: '/system', label: '系统状态' },
]

const riskLevel = computed(() => marketState.overview?.signal_risk?.market_risk_level ?? null)
const updatedText = computed(() => toRelative(marketState.lastUpdated))
</script>

<template>
  <header class="sticky top-0 z-20 bg-white/90 backdrop-blur border-b border-slate-200">
    <div class="w-full max-w-[1400px] mx-auto px-4 h-14 flex items-center justify-between gap-4">
      <div class="flex items-center gap-6 min-w-0">
        <router-link to="/" class="font-semibold text-slate-800 tracking-tight whitespace-nowrap">
          A股板块资金 · ETF 分析
        </router-link>
        <nav class="hidden sm:flex items-center gap-1">
          <router-link
            v-for="l in links"
            :key="l.to"
            :to="l.to"
            class="px-3 py-1.5 rounded-md text-sm text-slate-600 hover:bg-slate-100 hover:text-slate-900 transition"
            active-class="bg-slate-100 text-slate-900 font-medium"
          >
            {{ l.label }}
          </router-link>
        </nav>
      </div>

      <div class="flex items-center gap-3 text-xs text-slate-500 shrink-0">
        <span
          v-if="riskLevel"
          class="inline-flex items-center px-2 py-0.5 rounded-full border font-medium"
          :class="riskLevelBadge(riskLevel)"
        >
          风险{{ riskLevel }}
        </span>
        <span class="inline-flex items-center gap-1.5">
          <span
            class="w-2 h-2 rounded-full"
            :class="marketState.connected ? 'bg-emerald-500' : 'bg-rose-500'"
          />
          {{ marketState.connected ? '已连接' : '未连接' }}
        </span>
        <span class="tnum hidden md:inline">更新 {{ updatedText }}</span>
      </div>
    </div>

    <!-- 移动端导航 -->
    <nav class="sm:hidden flex items-center gap-1 px-4 pb-2 overflow-x-auto">
      <router-link
        v-for="l in links"
        :key="l.to"
        :to="l.to"
        class="px-3 py-1.5 rounded-md text-sm text-slate-600 hover:bg-slate-100 whitespace-nowrap"
        active-class="bg-slate-100 text-slate-900 font-medium"
      >
        {{ l.label }}
      </router-link>
    </nav>
  </header>
</template>
