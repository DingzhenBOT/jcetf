<script setup lang="ts">
import { onMounted, ref } from 'vue'
import Card from '@/components/ui/Card.vue'
import StatePanel from '@/components/ui/StatePanel.vue'
import { getOffExchange } from '@/api/endpoints'
import type { OffExchangeResult } from '@/api/types'

const data = ref<OffExchangeResult | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
const keyword = ref('ETF')

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    data.value = await getOffExchange(keyword.value, 10)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '未知错误'
  } finally {
    loading.value = false
  }
}
onMounted(load)

function pct(v: number | null | undefined): string {
  if (v == null || !isFinite(Number(v))) return '--'
  const n = Number(v)
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`
}
function cls(v: number | null | undefined): string {
  const n = Number(v)
  if (v == null || !isFinite(n) || n === 0) return 'text-slate-500'
  return n > 0 ? 'text-rose-600' : 'text-emerald-600'
}
</script>

<template>
  <div class="space-y-5">
    <div class="flex items-end justify-between flex-wrap gap-3">
      <div>
        <h1 class="text-xl font-semibold tracking-tight text-slate-800">场外基金</h1>
        <p class="text-sm text-slate-400 mt-0.5">开放式/场外基金检索（来源：盈米 yingmi-skill-cli）</p>
      </div>
      <div class="flex items-center gap-2">
        <input
          v-model="keyword"
          type="text"
          placeholder="关键词，如 ETF / 红利 / 债券"
          class="px-3 py-1.5 text-sm border border-slate-200 rounded-md focus:outline-none focus:ring-2 focus:ring-sky-200 w-52"
          @keyup.enter="load"
        />
        <button
          class="px-3 py-1.5 text-sm rounded-md bg-sky-600 text-white hover:bg-sky-700"
          @click="load"
        >
          搜索
        </button>
      </div>
    </div>

    <StatePanel :loading="loading" :error="error" @retry="load">
      <div
        v-if="data && !data.available"
        class="text-sm text-amber-600 bg-amber-50 border border-amber-200 rounded-md px-3 py-3"
      >
        {{ data.reason || '场外基金数据源暂不可用。' }}
        <div class="mt-1 text-xs text-slate-500">
          需在 CVM 安装并授权 盈米 CLI（<code>yingmi-skill-cli</code>）：执行其 CLI 前置检查与初始化后即可生效。
        </div>
      </div>

      <table v-else-if="data && data.items.length" class="w-full text-sm">
        <thead>
          <tr class="text-left text-xs text-slate-400 border-b border-slate-100">
            <th class="px-4 py-2 font-medium">代码</th>
            <th class="px-4 py-2 font-medium">名称</th>
            <th class="px-4 py-2 font-medium">类型</th>
            <th class="px-4 py-2 font-medium text-right">日涨幅</th>
            <th class="px-4 py-2 font-medium text-right">单位净值</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(f, i) in data.items" :key="f.code || i" class="border-b border-slate-50 hover:bg-slate-50/60">
            <td class="px-4 py-2 tnum text-slate-600">{{ f.code ?? '--' }}</td>
            <td class="px-4 py-2 text-slate-700">{{ f.name ?? '--' }}</td>
            <td class="px-4 py-2 text-slate-500">{{ f.type ?? '--' }}</td>
            <td class="px-4 py-2 text-right tnum font-medium" :class="cls(f.change_percent)">{{ pct(f.change_percent) }}</td>
            <td class="px-4 py-2 text-right tnum text-slate-600">{{ f.nav != null ? Number(f.nav).toFixed(4) : '--' }}</td>
          </tr>
        </tbody>
      </table>
      <div v-else class="py-10 text-center text-sm text-slate-400">暂无数据</div>
    </StatePanel>
  </div>
</template>
