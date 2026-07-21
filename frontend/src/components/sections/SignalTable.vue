<script setup lang="ts">
import { onMounted } from 'vue'
import type { Signal } from '@/api/types'
import Badge from '@/components/ui/Badge.vue'
import { TIER_BADGE, regimeText, listingBadge } from '@/lib/tier'
import { fmtScore, fmtConfidence } from '@/lib/format'
import { toBeijing } from '@/lib/time'
import { ensureEtfNames, etfName, etfListing } from '@/composables/etfNames'

defineProps<{
  signals: Signal[]
  showEtf?: boolean
}>()

// 拉取 ETF 名称/场所映射，供信号表按代码显示名称（而非裸代码）。
onMounted(() => {
  void ensureEtfNames()
})
</script>

<template>
  <div class="overflow-x-auto -mx-4">
    <table class="w-full text-sm">
      <thead>
        <tr class="text-left text-xs text-slate-400 border-b border-slate-100">
          <th v-if="showEtf" class="px-4 py-2 font-medium">ETF</th>
          <th class="px-4 py-2 font-medium">档位</th>
          <th class="px-4 py-2 font-medium">综合分</th>
          <th class="px-4 py-2 font-medium">置信</th>
          <th class="px-4 py-2 font-medium">市场环境</th>
          <th class="px-4 py-2 font-medium">建议仓位</th>
          <th class="px-4 py-2 font-medium">生成时间</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="s in signals"
          :key="s.signal_id"
          class="border-b border-slate-50 hover:bg-slate-50/60"
        >
          <td v-if="showEtf" class="px-4 py-2">
            <div class="flex flex-col gap-0.5">
              <router-link
                :to="`/etfs/${s.target_etf}`"
                class="text-slate-700 hover:text-sky-600 font-medium leading-tight"
              >
                {{ etfName(s.target_etf) }}
              </router-link>
              <div class="flex items-center gap-1.5">
                <span class="text-xs text-slate-400 tnum">{{ s.target_etf }}</span>
                <Badge
                  v-if="etfListing(s.target_etf)"
                  :text="etfListing(s.target_etf) ?? ''"
                  :class="listingBadge(etfListing(s.target_etf))"
                />
              </div>
            </div>
          </td>
          <td class="px-4 py-2">
            <Badge :text="s.signal_type_text" :class="TIER_BADGE[s.signal_type]" />
          </td>
          <td class="px-4 py-2 tnum text-slate-700">{{ fmtScore(s.score) }}</td>
          <td class="px-4 py-2 tnum text-slate-500">{{ fmtConfidence(s.confidence) }}</td>
          <td class="px-4 py-2 text-slate-600">{{ regimeText(s.market_regime) }}</td>
          <td class="px-4 py-2 text-slate-600">{{ s.position_text }}</td>
          <td class="px-4 py-2 tnum text-slate-400 whitespace-nowrap">{{ toBeijing(s.generated_at) }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
