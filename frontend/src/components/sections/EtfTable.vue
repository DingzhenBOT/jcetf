<script setup lang="ts">
import { useRouter } from 'vue-router'
import type { EtfListItem } from '@/api/types'
import Badge from '@/components/ui/Badge.vue'
import { TIER_BADGE, regimeText, listingBadge } from '@/lib/tier'
import { fmtScore } from '@/lib/format'

defineProps<{ etfs: EtfListItem[] }>()
const router = useRouter()
</script>

<template>
  <div class="overflow-x-auto -mx-4">
    <table class="w-full text-sm">
      <thead>
        <tr class="text-left text-xs text-slate-400 border-b border-slate-100">
          <th class="px-4 py-2 font-medium">代码</th>
          <th class="px-4 py-2 font-medium">名称</th>
          <th class="px-4 py-2 font-medium">分类</th>
          <th class="px-4 py-2 font-medium">场所</th>
          <th class="px-4 py-2 font-medium">最新信号</th>
          <th class="px-4 py-2 font-medium">综合分</th>
          <th class="px-4 py-2 font-medium text-right">当日涨幅</th>
          <th class="px-4 py-2 font-medium">市场环境</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="e in etfs"
          :key="e.etf_code"
          class="border-b border-slate-50 hover:bg-slate-50/60 cursor-pointer"
          @click="router.push(`/etfs/${e.etf_code}`)"
        >
          <td class="px-4 py-2">
            <router-link
              :to="`/etfs/${e.etf_code}`"
              class="font-medium text-slate-700 hover:text-sky-600"
              @click.stop
            >
              {{ e.etf_code }}
            </router-link>
          </td>
          <td class="px-4 py-2 text-slate-600">{{ e.etf_name ?? '--' }}</td>
          <td class="px-4 py-2 text-slate-500">{{ e.category ?? '--' }}</td>
          <td class="px-4 py-2">
            <Badge
              v-if="e.listing"
              :text="e.listing"
              :class="listingBadge(e.listing)"
            />
            <span v-else class="text-slate-300">--</span>
          </td>
          <td class="px-4 py-2">
            <Badge
              v-if="e.latest_signal"
              :text="e.latest_signal.signal_type_text"
              :class="TIER_BADGE[e.latest_signal.signal_type]"
            />
            <span v-else class="text-slate-300">暂无信号</span>
          </td>
          <td class="px-4 py-2 tnum text-slate-700">
            {{ e.latest_signal ? fmtScore(e.latest_signal.score) : '--' }}
          </td>
          <td
            class="px-4 py-2 tnum text-right font-medium"
            :class="(e.change_percent ?? 0) >= 0 ? 'text-rose-600' : 'text-emerald-600'"
          >
            <span v-if="e.change_percent != null">{{ e.change_percent >= 0 ? '+' : '' }}{{ e.change_percent.toFixed(2) }}%</span>
            <span v-else class="text-slate-300">--</span>
          </td>
          <td class="px-4 py-2 text-slate-500">
            {{ e.latest_signal ? regimeText(e.latest_signal.market_regime) : '--' }}
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
