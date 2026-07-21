<script setup lang="ts">
// 意见列表（UX 改进第1层）：人话内容置顶；触发依据用原生 <details> 渐进披露。
import type { Opinion } from '@/api/types'
import { toBeijing } from '@/lib/time'
import { phaseText } from '@/lib/tier'

defineProps<{ opinions: Opinion[] }>()

// 将 input_summary 拍平为 "key: value" 行，作为可展开的"依据"。
function summaryLines(o: Opinion): string[] {
  const s = o.input_summary
  if (!s) return []
  return Object.entries(s).map(([k, v]) => {
    const val = typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v)
    return `${k}：${val}`
  })
}
</script>

<template>
  <ul class="space-y-3">
    <li
      v-for="o in opinions"
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
      <details v-if="summaryLines(o).length" class="group mt-2">
        <summary class="cursor-pointer text-xs text-slate-400 hover:text-slate-600">查看依据</summary>
        <ul class="mt-1 space-y-0.5 border-l border-slate-100 pl-3 text-xs text-slate-500">
          <li v-for="(line, i) in summaryLines(o)" :key="i">{{ line }}</li>
        </ul>
      </details>
    </li>
  </ul>
</template>
