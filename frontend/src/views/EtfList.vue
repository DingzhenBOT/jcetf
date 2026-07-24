<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import Card from '@/components/ui/Card.vue'
import StatePanel from '@/components/ui/StatePanel.vue'
import EtfTable from '@/components/sections/EtfTable.vue'
import { getEtfs } from '@/api/endpoints'
import type { EtfListItem } from '@/api/types'
import { marketState } from '@/stores/market'

const etfs = ref<EtfListItem[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const keyword = ref('')
const categoryFilter = ref<string>('全部')
const listingFilter = ref<string>('全部')
const sortKey = ref<'score' | 'change'>('score') // 综合分 / 当日涨幅

function sortValue(e: EtfListItem): number {
  if (sortKey.value === 'change') return e.change_percent ?? -Infinity
  return e.latest_signal?.score ?? -Infinity
}

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    etfs.value = await getEtfs()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '未知错误'
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(
  () => marketState.lastUpdated,
  () => {
    if (marketState.connected) void load()
  },
)

const categories = computed(() => [
  '全部',
  ...new Set(etfs.value.map((e) => e.category ?? '未分类')),
])
const listings = ['全部', '场内', '场外']
const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  return etfs.value
    .filter(
      (e) =>
        categoryFilter.value === '全部' || (e.category ?? '未分类') === categoryFilter.value,
    )
    .filter(
      (e) => listingFilter.value === '全部' || (e.listing ?? '') === listingFilter.value,
    )
    .filter(
      (e) =>
        !kw ||
        e.etf_code.toLowerCase().includes(kw) ||
        (e.etf_name ?? '').toLowerCase().includes(kw),
    )
    .sort((a, b) => sortValue(b) - sortValue(a))
})
</script>

<template>
  <div class="space-y-5">
    <div>
      <h1 class="text-xl font-semibold tracking-tight text-slate-800">ETF 列表</h1>
      <p class="text-sm text-slate-400 mt-0.5">
        精选 ETF 映射与最新信号摘要（共 {{ etfs.length }} 支）
      </p>
    </div>

    <div class="flex items-center gap-3 flex-wrap">
      <input
        v-model="keyword"
        type="text"
        placeholder="搜索代码 / 名称"
        class="px-3 py-1.5 text-sm border border-slate-200 rounded-md focus:outline-none focus:ring-2 focus:ring-sky-200 w-48"
      />
      <select
        v-model="categoryFilter"
        class="px-3 py-1.5 text-sm border border-slate-200 rounded-md bg-white focus:outline-none focus:ring-2 focus:ring-sky-200"
      >
        <option v-for="c in categories" :key="c" :value="c">{{ c }}</option>
      </select>
      <select
        v-model="listingFilter"
        class="px-3 py-1.5 text-sm border border-slate-200 rounded-md bg-white focus:outline-none focus:ring-2 focus:ring-sky-200"
      >
        <option v-for="l in listings" :key="l" :value="l">{{ l === '全部' ? '全部场所' : l }}</option>
      </select>
      <div class="flex items-center rounded-md border border-slate-200 overflow-hidden text-sm">
        <button
          type="button"
          class="px-3 py-1.5"
          :class="sortKey === 'score' ? 'bg-sky-600 text-white' : 'bg-white text-slate-500 hover:bg-slate-50'"
          @click="sortKey = 'score'"
        >
          综合分
        </button>
        <button
          type="button"
          class="px-3 py-1.5 border-l border-slate-200"
          :class="sortKey === 'change' ? 'bg-sky-600 text-white' : 'bg-white text-slate-500 hover:bg-slate-50'"
          @click="sortKey = 'change'"
        >
          当日涨幅
        </button>
      </div>
      <span class="text-xs text-slate-400">命中 {{ filtered.length }} 支</span>
    </div>

    <Card>
      <StatePanel
        :loading="loading"
        :error="error"
        :empty="!loading && filtered.length === 0"
        empty-text="无匹配 ETF"
        @retry="load"
      >
        <EtfTable :etfs="filtered" />
      </StatePanel>
    </Card>
  </div>
</template>
