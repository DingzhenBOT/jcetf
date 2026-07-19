<script setup lang="ts">
import type { PortfolioAnalyzeItem } from '@/api/types'
import Card from '@/components/ui/Card.vue'
import { ACTION_BADGE, ACTION_TEXT } from '@/lib/tier'
import { changeColor, fmtInt } from '@/lib/format'
import { toBeijing } from '@/lib/time'

defineProps<{ items: PortfolioAnalyzeItem[] }>()

function signMoney(v: number): string {
  const s = fmtInt(Math.abs(v))
  return v >= 0 ? `+¥${s}` : `-¥${s}`
}
</script>

<template>
  <div class="space-y-4">
    <Card
      v-for="it in items"
      :key="it.etf_code"
      :title="it.etf_code"
      :subtitle="`复核时间 ${it.review_time ? toBeijing(it.review_time) : '—'}`"
    >
      <template #actions>
        <span
          class="inline-flex items-center px-2.5 py-1 rounded-full border text-xs font-medium"
          :class="ACTION_BADGE[it.action]"
        >
          {{ ACTION_TEXT[it.action] }}
        </span>
      </template>

      <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
        <div v-if="it.return_percent !== null && it.return_percent !== undefined">
          <div class="text-slate-400 text-xs">收益率</div>
          <div class="tnum font-semibold" :class="changeColor(it.return_percent)">
            {{ it.return_percent > 0 ? '+' : '' }}{{ it.return_percent.toFixed(2) }}%
          </div>
        </div>
        <div v-if="it.pnl_amount !== null && it.pnl_amount !== undefined">
          <div class="text-slate-400 text-xs">盈亏金额</div>
          <div class="tnum font-semibold" :class="changeColor(it.pnl_amount)">
            {{ signMoney(it.pnl_amount) }}
          </div>
        </div>
        <div v-if="it.suggested_position_text">
          <div class="text-slate-400 text-xs">建议仓位</div>
          <div class="font-semibold text-slate-700">{{ it.suggested_position_text }}</div>
        </div>
        <div v-if="it.suggested_position_range && it.suggested_position_range.length === 2">
          <div class="text-slate-400 text-xs">建议区间</div>
          <div class="tnum font-semibold text-slate-700">
            {{ it.suggested_position_range[0] }}–{{ it.suggested_position_range[1] }}%
          </div>
        </div>
        <div v-if="it.return_percent === null || it.return_percent === undefined">
          <div class="text-slate-400 text-xs">收益率</div>
          <div class="text-slate-300">—（无行情）</div>
        </div>
      </div>

      <div class="mt-3 text-sm text-slate-600">
        <span class="text-slate-400">理由：</span>{{ it.reason }}
      </div>
      <div class="mt-1 text-sm text-slate-600">
        <span class="text-slate-400">风险：</span>{{ it.risk }}
      </div>

      <div
        v-if="it.invalidation_conditions?.length"
        class="mt-3 text-xs text-amber-600 bg-amber-50 border border-amber-200 rounded-md px-3 py-2"
      >
        <div class="font-medium mb-1">触发失效条件：</div>
        <ul class="space-y-0.5">
          <li v-for="(c, i) in it.invalidation_conditions" :key="i">· {{ c }}</li>
        </ul>
      </div>
    </Card>
  </div>
</template>
