<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import Card from '@/components/ui/Card.vue'
import StatePanel from '@/components/ui/StatePanel.vue'
import Badge from '@/components/ui/Badge.vue'
import { getEtfs } from '@/api/endpoints'
import type { EtfListItem, SignalType } from '@/api/types'
import { TIER_TEXT, TIER_BADGE, TIER_ORDER } from '@/lib/tier'
import { fmtScore } from '@/lib/format'
import { marketState } from '@/stores/market'

const etfs = ref<EtfListItem[]>([])
const loading = ref(false)
const error = ref<string | null>(null)

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
// 随全局 30s 轮询刷新 ETF 列表
watch(
  () => marketState.lastUpdated,
  () => {
    if (marketState.connected) void load()
  },
)

interface Group {
  category: string
  etfs: EtfListItem[]
  total: number
  withSignal: number
  dominant?: SignalType
  avgScore: number | null
}

const groups = computed<Group[]>(() => {
  const map = new Map<string, EtfListItem[]>()
  for (const e of etfs.value) {
    const c = e.category ?? '未分类'
    if (!map.has(c)) map.set(c, [])
    map.get(c)!.push(e)
  }
  const result: Group[] = []
  for (const [category, list] of map) {
    const withSig = list.filter((e) => e.latest_signal)
    let dominant: SignalType | undefined
    if (withSig.length) {
      dominant = withSig.reduce((a, b) =>
        TIER_ORDER[b.latest_signal!.signal_type] > TIER_ORDER[a.latest_signal!.signal_type] ? b : a,
      ).latest_signal!.signal_type
    }
    const scores = withSig
      .map((e) => e.latest_signal!.score)
      .filter((s): s is number => s != null)
    const avgScore = scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : null
    result.push({ category, etfs: list, total: list.length, withSignal: withSig.length, dominant, avgScore })
  }
  return result.sort((a, b) => b.withSignal - a.withSignal)
})

// 关联板块聚合（来自 related_sector_codes，非实时板块排行）
const sectorAgg = computed<{ code: string; count: number }[]>(() => {
  const m = new Map<string, number>()
  for (const e of etfs.value) {
    for (const c of e.related_sector_codes ?? []) {
      m.set(c, (m.get(c) ?? 0) + 1)
    }
  }
  return [...m.entries()]
    .map(([code, count]) => ({ code, count }))
    .sort((a, b) => b.count - a.count)
})
</script>

<template>
  <div class="space-y-5">
    <div>
      <h1 class="text-xl font-semibold tracking-tight text-slate-800">板块 / 行业分组</h1>
      <p class="text-sm text-slate-400 mt-0.5">
        依据 ETF 映射的 <span class="text-slate-500">category</span> 与
        <span class="text-slate-500">related_sector_codes</span> 聚合（板块实时排行接口将于后续阶段接入）。
      </p>
    </div>

    <StatePanel
      :loading="loading"
      :error="error"
      :empty="!loading && etfs.length === 0"
      empty-text="暂无 ETF 映射数据"
      @retry="load"
    >
      <div class="space-y-4">
        <Card
          v-for="g in groups"
          :key="g.category"
          :title="g.category"
          :subtitle="`${g.total} 支 ETF · ${g.withSignal} 支有信号`"
        >
          <div class="flex items-center gap-3 mb-3 flex-wrap">
            <span v-if="g.dominant" class="text-xs">
              主导信号：<Badge :text="TIER_TEXT[g.dominant]" :class="TIER_BADGE[g.dominant]" />
            </span>
            <span v-if="g.avgScore != null" class="text-xs text-slate-500">
              平均综合分
              <span class="tnum font-medium text-slate-700">{{ fmtScore(g.avgScore) }}</span>
            </span>
          </div>
          <div class="flex flex-wrap gap-2">
            <router-link
              v-for="e in g.etfs"
              :key="e.etf_code"
              :to="`/etfs/${e.etf_code}`"
              class="inline-flex items-center gap-2 px-2.5 py-1 rounded-md border border-slate-200 text-sm hover:bg-slate-50 transition active:scale-[0.98]"
            >
              <span class="text-slate-700">{{ e.etf_name ?? e.etf_code }}</span>
              <span
                v-if="e.latest_signal"
                class="text-xs"
                :class="TIER_BADGE[e.latest_signal.signal_type]"
              >
                {{ e.latest_signal.signal_type_text }}
              </span>
              <span v-else class="text-xs text-slate-300">—</span>
            </router-link>
          </div>
        </Card>
      </div>

      <Card
        v-if="sectorAgg.length"
        class="mt-4"
        title="关联板块代码（来自 ETF 映射）"
        subtitle="related_sector_codes 聚合，非实时板块排行"
      >
        <div class="flex flex-wrap gap-2">
          <span
            v-for="s in sectorAgg"
            :key="s.code"
            class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-slate-100 text-sm text-slate-600"
          >
            {{ s.code }}
            <span class="text-xs text-slate-400">×{{ s.count }}</span>
          </span>
        </div>
      </Card>
    </StatePanel>
  </div>
</template>
