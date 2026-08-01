<script setup lang="ts">
// 意见列表（UX 改进第1层）：人话内容置顶；触发依据用原生 <details> 渐进披露。
// 折叠：默认只显示最新一条（按 generated_at 降序），其余收起，点击「查看历史」展开全部。
import { computed, ref } from 'vue'
import type { Opinion } from '@/api/types'
import { toBeijing } from '@/lib/time'
import { phaseText } from '@/lib/tier'

const props = defineProps<{ opinions: Opinion[] }>()

const expanded = ref(false)

// 按生成时间降序（最新在前）
const sorted = computed<Opinion[]>(() =>
  [...(props.opinions ?? [])].sort(
    (a, b) => new Date(b.generated_at).getTime() - new Date(a.generated_at).getTime(),
  ),
)
const latest = computed<Opinion | null>(() => sorted.value[0] ?? null)
const rest = computed<Opinion[]>(() => sorted.value.slice(1))
// 折叠时仅显示最新一条；展开后显示全部
const displayList = computed<Opinion[]>(() =>
  expanded.value ? sorted.value : latest.value ? [latest.value] : [],
)

// 将 input_summary 拍平为 "key: value" 行，作为「分析依据」下的次级"原始参数"折叠。
function summaryLines(o: Opinion): string[] {
  const s = o.input_summary
  if (!s) return []
  return Object.entries(s).map(([k, v]) => {
    const val = typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v)
    return `${k}：${val}`
  })
}

function fmtPrice(x: number | null | undefined): string {
  if (x == null) return '—'
  return x.toFixed(3)
}
</script>

<template>
  <div>
    <div v-if="rest.length" class="mb-2 flex justify-end">
      <button
        v-if="!expanded"
        type="button"
        class="text-xs text-sky-600 hover:underline"
        @click="expanded = true"
      >
        查看历史（{{ rest.length }}）
      </button>
      <button
        v-else
        type="button"
        class="text-xs text-slate-400 hover:underline"
        @click="expanded = false"
      >
        收起历史
      </button>
    </div>
    <ul class="space-y-3">
      <li
        v-for="o in displayList"
        :key="o.opinion_id"
        class="rounded-lg border border-slate-200 p-3"
      >
        <div class="mb-1.5 flex items-center justify-between">
          <span class="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-500">
            {{ phaseText(o.phase) }}
          </span>
          <span class="tnum text-xs text-slate-400">{{ toBeijing(o.generated_at) }}</span>
        </div>
        <h4 v-if="o.title" class="text-sm font-medium text-slate-700">{{ o.title }}</h4>
        <p class="mt-1 whitespace-pre-line text-sm leading-relaxed text-slate-600">
          {{ o.content ?? '（无内容）' }}
        </p>
        <!-- 收盘后三档价位（C23）：突破/加仓/止损 + 明日预期 -->
        <div v-if="o.trade_plan" class="mt-2 rounded-md border border-slate-200 bg-slate-50 p-2.5">
          <div class="mb-1.5 text-xs font-medium text-slate-500">明日三档操作参考</div>
          <div class="grid grid-cols-1 gap-1.5 text-xs">
            <div v-if="o.trade_plan.breakout_price != null" class="flex items-center justify-between gap-2">
              <span class="shrink-0 text-rose-600">突破上车</span>
              <span class="tnum text-right font-semibold text-slate-700">
                {{ fmtPrice(o.trade_plan.breakout_price) }}
                <span class="font-normal text-slate-400">· {{ o.trade_plan.breakout_cond }}</span>
              </span>
            </div>
            <div v-if="o.trade_plan.add_price != null" class="flex items-center justify-between gap-2">
              <span class="shrink-0 text-emerald-600">回踩加仓</span>
              <span class="tnum text-right font-semibold text-slate-700">
                {{ fmtPrice(o.trade_plan.add_price) }}
                <span class="font-normal text-slate-400">· {{ o.trade_plan.add_cond }}</span>
              </span>
            </div>
            <div v-if="o.trade_plan.stop_price != null" class="flex items-center justify-between gap-2">
              <span class="shrink-0 text-amber-600">跌破止损</span>
              <span class="tnum text-right font-semibold text-slate-700">
                {{ fmtPrice(o.trade_plan.stop_price) }}
                <span class="font-normal text-slate-400">· {{ o.trade_plan.stop_cond }}</span>
              </span>
            </div>
            <div v-if="o.trade_plan.expectation_low != null && o.trade_plan.expectation_high != null" class="flex items-center justify-between gap-2">
              <span class="shrink-0 text-slate-500">明日预期区间</span>
              <span class="tnum text-right text-slate-700">
                {{ fmtPrice(o.trade_plan.expectation_low) }} ~ {{ fmtPrice(o.trade_plan.expectation_high) }}
                <span class="font-normal text-slate-400">· {{ o.trade_plan.regime_tomorrow }}</span>
              </span>
            </div>
          </div>
        </div>
        <details v-if="o.basis_text || summaryLines(o).length" class="group mt-2">
          <summary class="cursor-pointer text-xs text-slate-400 hover:text-slate-600">查看依据</summary>
          <p v-if="o.basis_text" class="mt-1.5 whitespace-pre-line border-l border-slate-200 pl-3 text-xs leading-relaxed text-slate-600">
            {{ o.basis_text }}
          </p>
          <details v-if="summaryLines(o).length" class="group mt-1.5">
            <summary class="cursor-pointer text-[11px] text-slate-400 hover:text-slate-600">原始信号参数</summary>
            <ul class="mt-1 space-y-0.5 border-l border-slate-100 pl-3 text-[11px] text-slate-500">
              <li v-for="(line, i) in summaryLines(o)" :key="i">{{ line }}</li>
            </ul>
          </details>
        </details>
      </li>
    </ul>

    <div v-if="!latest" class="text-sm text-slate-400 py-4">暂无意见</div>
  </div>
</template>
