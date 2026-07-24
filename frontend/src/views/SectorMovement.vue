<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import Card from '@/components/ui/Card.vue'
import StatePanel from '@/components/ui/StatePanel.vue'
import { getSectorMovement } from '@/api/endpoints'
import type { SectorMovement } from '@/api/types'

const data = ref<SectorMovement | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)

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
onMounted(load)

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

const industry = computed(() => data.value?.industry ?? [])
const concept = computed(() => data.value?.concept ?? [])
const fundFlow = computed(() => data.value?.fund_flow ?? [])
const degraded = computed(() => data.value != null && data.value.available === false)
</script>

<template>
  <div class="space-y-5">
    <div>
      <h1 class="text-xl font-semibold tracking-tight text-slate-800">板块异动</h1>
      <p class="text-sm text-slate-400 mt-0.5">
        行业/概念涨幅排名与领涨股、行业资金流入（来源：腾讯自选股）
      </p>
    </div>

    <StatePanel :loading="loading" :error="error" @retry="load">
      <div v-if="degraded" class="text-sm text-amber-600 bg-amber-50 border border-amber-200 rounded-md px-3 py-2 mb-3">
        板块数据源暂不可用（npx westock-data 未运行或网络受限），已降级显示。
      </div>

      <div v-if="data" class="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card title="行业板块涨幅" :subtitle="`${industry.length} 个`">
          <table class="w-full text-sm">
            <thead>
              <tr class="text-left text-xs text-slate-400 border-b border-slate-100">
                <th class="py-2 font-medium">板块</th>
                <th class="py-2 font-medium text-right">涨幅</th>
                <th class="py-2 font-medium text-right">5日</th>
                <th class="py-2 font-medium">领涨股</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="s in industry" :key="s.name" class="border-b border-slate-50">
                <td class="py-1.5 text-slate-700">{{ s.name }}</td>
                <td class="py-1.5 text-right tnum font-medium" :class="cls(s.changePct)">{{ pct(s.changePct) }}</td>
                <td class="py-1.5 text-right tnum" :class="cls(s.changePct5d)">{{ pct(s.changePct5d) }}</td>
                <td class="py-1.5 text-xs text-slate-500">{{ s.leadStock ?? '--' }}</td>
              </tr>
              <tr v-if="!industry.length"><td colspan="4" class="py-3 text-center text-slate-400">暂无数据</td></tr>
            </tbody>
          </table>
        </Card>

        <Card title="概念板块涨幅" :subtitle="`${concept.length} 个`">
          <table class="w-full text-sm">
            <thead>
              <tr class="text-left text-xs text-slate-400 border-b border-slate-100">
                <th class="py-2 font-medium">板块</th>
                <th class="py-2 font-medium text-right">涨幅</th>
                <th class="py-2 font-medium text-right">5日</th>
                <th class="py-2 font-medium">领涨股</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="s in concept" :key="s.name" class="border-b border-slate-50">
                <td class="py-1.5 text-slate-700">{{ s.name }}</td>
                <td class="py-1.5 text-right tnum font-medium" :class="cls(s.changePct)">{{ pct(s.changePct) }}</td>
                <td class="py-1.5 text-right tnum" :class="cls(s.changePct5d)">{{ pct(s.changePct5d) }}</td>
                <td class="py-1.5 text-xs text-slate-500">{{ s.leadStock ?? '--' }}</td>
              </tr>
              <tr v-if="!concept.length"><td colspan="4" class="py-3 text-center text-slate-400">暂无数据</td></tr>
            </tbody>
          </table>
        </Card>

        <Card class="lg:col-span-2" title="行业资金流入 Top" :subtitle="`${fundFlow.length} 个`">
          <table class="w-full text-sm">
            <thead>
              <tr class="text-left text-xs text-slate-400 border-b border-slate-100">
                <th class="py-2 font-medium">板块</th>
                <th class="py-2 font-medium text-right">涨幅</th>
                <th class="py-2 font-medium text-right">主力净流入(万)</th>
                <th class="py-2 font-medium text-right">5日主力净流入(万)</th>
                <th class="py-2 font-medium text-right">涨跌比</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="s in fundFlow" :key="s.name" class="border-b border-slate-50">
                <td class="py-1.5 text-slate-700">{{ s.name }}</td>
                <td class="py-1.5 text-right tnum font-medium" :class="cls(s.changePct)">{{ pct(s.changePct) }}</td>
                <td class="py-1.5 text-right tnum" :class="cls(s.mainNetInflow)">{{ s.mainNetInflow != null ? Number(s.mainNetInflow).toFixed(0) : '--' }}</td>
                <td class="py-1.5 text-right tnum" :class="cls(s.mainNetInflow5d)">{{ s.mainNetInflow5d != null ? Number(s.mainNetInflow5d).toFixed(0) : '--' }}</td>
                <td class="py-1.5 text-right tnum text-slate-500">{{ s.upDownRatio ?? '--' }}</td>
              </tr>
              <tr v-if="!fundFlow.length"><td colspan="5" class="py-3 text-center text-slate-400">暂无数据</td></tr>
            </tbody>
          </table>
        </Card>
      </div>
    </StatePanel>
  </div>
</template>
