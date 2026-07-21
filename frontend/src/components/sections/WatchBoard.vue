<script setup lang="ts">
// 今日关注榜（UX 改进第1层）：把"可操作"信号前置到第一屏。
// 取 latestSignals，按档位积极度降序取 TOP5（OPPORTUNITY_ENHANCE / SMALL_POSITION），
// 每条卡片：ETF 名 + 档位徽章(大字) + 一句人话(one_liner) + 建议仓位。
// 不引入新请求（复用 store 已轮询的 latestSignals）；不动引擎/评分。
import { computed, onMounted } from 'vue'
import type { Signal, SignalType } from '@/api/types'
import Badge from '@/components/ui/Badge.vue'
import { TIER_BADGE, TIER_ORDER, listingBadge } from '@/lib/tier'
import { ensureEtfNames, etfName, etfListing } from '@/composables/etfNames'

const props = defineProps<{ signals: Signal[] }>()

const ACTIONABLE: SignalType[] = ['OPPORTUNITY_ENHANCE', 'SMALL_POSITION']
const AVOID: SignalType[] = ['NO_PARTICIPATE', 'NO_CHASE_HIGH', 'MARKET_RISK_HIGH']

const actionable = computed(() =>
  props.signals
    .filter((s) => ACTIONABLE.includes(s.signal_type))
    .sort((a, b) => {
      const o = (TIER_ORDER[b.signal_type] ?? 0) - (TIER_ORDER[a.signal_type] ?? 0)
      return o !== 0 ? o : (b.score ?? 0) - (a.score ?? 0)
    })
    .slice(0, 5),
)

const observeCount = computed(
  () => props.signals.filter((s) => s.signal_type === 'OBSERVE').length,
)
const avoidCount = computed(
  () => props.signals.filter((s) => AVOID.includes(s.signal_type)).length,
)

onMounted(() => void ensureEtfNames())
</script>

<template>
  <div>
    <div v-if="actionable.length" class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
      <router-link
        v-for="s in actionable"
        :key="s.signal_id"
        :to="`/etfs/${s.target_etf}`"
        class="block rounded-xl border border-slate-200 bg-white p-3.5 transition hover:border-slate-300 hover:shadow-sm active:scale-[0.98]"
      >
        <div class="flex items-center justify-between gap-2">
          <div class="min-w-0">
            <div class="flex items-center gap-1.5">
              <span class="font-medium text-slate-800 truncate">{{ etfName(s.target_etf) }}</span>
              <Badge
                v-if="etfListing(s.target_etf)"
                :text="etfListing(s.target_etf) ?? ''"
                :class="listingBadge(etfListing(s.target_etf))"
              />
            </div>
            <div class="tnum mt-0.5 text-xs text-slate-400">{{ s.target_etf }}</div>
          </div>
          <Badge :text="s.signal_type_text" :class="TIER_BADGE[s.signal_type]" />
        </div>
        <p class="mt-2 line-clamp-2 text-xs leading-relaxed text-slate-500">
          {{ s.one_liner ?? s.position_text }}
        </p>
        <div class="mt-1.5 text-xs text-slate-400">{{ s.position_text }}</div>
      </router-link>
    </div>
    <div
      v-else
      class="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-6 text-center text-sm text-slate-500"
    >
      当前无可操作机会，以观察 / 规避为主
      <span v-if="observeCount || avoidCount" class="text-slate-400">
        （观察 {{ observeCount }} · 规避 {{ avoidCount }}）</span
      >
    </div>
  </div>
</template>
