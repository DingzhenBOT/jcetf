<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import type { EChartsOption } from 'echarts'
import BaseChart from '@/components/charts/BaseChart.vue'
import Card from '@/components/ui/Card.vue'
import StatePanel from '@/components/ui/StatePanel.vue'
import { getOffExchangeDetail } from '@/api/endpoints'
import type { OffExchangeDetailResult } from '@/api/types'

const route = useRoute()
const code = computed(() => String(route.params.code || ''))
const data = ref<OffExchangeDetailResult | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    data.value = await getOffExchangeDetail(code.value)
    if (!data.value.available) error.value = data.value.reason || '场外基金资料暂不可用'
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(code, load)

const latest = computed(() => data.value?.nav_history.at(-1) ?? null)
const recentRows = computed(() => [...(data.value?.nav_history ?? [])].reverse().slice(0, 20))
const chartOption = computed<EChartsOption>(() => ({
  animation: false,
  tooltip: { trigger: 'axis' },
  grid: { left: 18, right: 18, top: 24, bottom: 42, containLabel: true },
  xAxis: {
    type: 'category',
    boundaryGap: false,
    data: (data.value?.nav_history ?? []).map((p) => p.date),
    axisLabel: { color: '#94a3b8', hideOverlap: true },
  },
  yAxis: { type: 'value', scale: true, axisLabel: { color: '#94a3b8' } },
  dataZoom: [{ type: 'inside' }, { type: 'slider', height: 18, bottom: 5 }],
  series: [{
    name: '单位净值',
    type: 'line',
    showSymbol: false,
    smooth: false,
    lineStyle: { color: '#0284c7', width: 2 },
    areaStyle: { color: 'rgba(14,165,233,0.10)' },
    data: (data.value?.nav_history ?? []).map((p) => p.nav),
  }],
}))

function pct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(Number(v))) return '--'
  const n = Number(v)
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`
}
</script>

<template>
  <div class="space-y-5">
    <router-link to="/offexchange" class="text-sm text-sky-600 hover:underline">← 返回场外基金搜索</router-link>
    <StatePanel :loading="loading" :error="error" @retry="load">
      <template v-if="data?.fund">
        <div>
          <div class="flex items-center gap-2 flex-wrap">
            <h1 class="text-xl font-semibold text-slate-800">{{ data.fund.code }}</h1>
            <span class="text-slate-600">{{ data.fund.name }}</span>
            <span v-if="data.fund.type" class="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-500">{{ data.fund.type }}</span>
          </div>
          <p class="mt-1 text-xs text-slate-400">{{ data.source }}</p>
        </div>

        <Card title="最新净值" :subtitle="latest?.date || '暂无净值日期'">
          <div class="grid grid-cols-2 gap-4 max-w-md">
            <div>
              <div class="text-xs text-slate-400">单位净值</div>
              <div class="mt-1 text-2xl font-semibold tnum text-slate-800">{{ latest?.nav?.toFixed(4) ?? '--' }}</div>
            </div>
            <div>
              <div class="text-xs text-slate-400">日增长率</div>
              <div class="mt-1 text-2xl font-semibold tnum" :class="(latest?.change_percent ?? 0) >= 0 ? 'text-rose-600' : 'text-emerald-600'">
                {{ pct(latest?.change_percent) }}
              </div>
            </div>
          </div>
          <p v-if="data.reason" class="mt-3 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-700">{{ data.reason }}</p>
        </Card>

        <Card title="净值走势" :subtitle="data.nav_history.length ? `最近 ${data.nav_history.length} 个净值日` : ''">
          <BaseChart v-if="data.nav_history.length" :option="chartOption" height="340px" />
          <div v-else class="py-10 text-center text-sm text-slate-400">基金资料已找到，但净值历史暂不可用。</div>
        </Card>

        <Card v-if="recentRows.length" title="近期净值">
          <table class="w-full text-sm">
            <thead><tr class="border-b border-slate-100 text-left text-xs text-slate-400"><th class="py-2">日期</th><th class="py-2 text-right">单位净值</th><th class="py-2 text-right">日增长率</th></tr></thead>
            <tbody>
              <tr v-for="p in recentRows" :key="p.date" class="border-b border-slate-50">
                <td class="py-2 tnum text-slate-500">{{ p.date }}</td>
                <td class="py-2 text-right tnum text-slate-700">{{ p.nav.toFixed(4) }}</td>
                <td class="py-2 text-right tnum" :class="(p.change_percent ?? 0) >= 0 ? 'text-rose-600' : 'text-emerald-600'">{{ pct(p.change_percent) }}</td>
              </tr>
            </tbody>
          </table>
        </Card>
      </template>
    </StatePanel>
  </div>
</template>
