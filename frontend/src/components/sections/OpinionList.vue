<script setup lang="ts">
import type { Opinion } from '@/api/types'
import { toBeijing } from '@/lib/time'
import { phaseText } from '@/lib/tier'

defineProps<{ opinions: Opinion[] }>()
</script>

<template>
  <ul class="space-y-3">
    <li
      v-for="o in opinions"
      :key="o.opinion_id"
      class="border border-slate-200 rounded-lg p-3"
    >
      <div class="flex items-center justify-between mb-1.5">
        <span class="text-xs px-2 py-0.5 rounded-full bg-slate-100 text-slate-500">
          {{ phaseText(o.phase) }}
        </span>
        <span class="text-xs text-slate-400 tnum">{{ toBeijing(o.generated_at) }}</span>
      </div>
      <h4 v-if="o.title" class="text-sm font-medium text-slate-700">{{ o.title }}</h4>
      <p class="text-sm text-slate-600 leading-relaxed mt-1 whitespace-pre-line">
        {{ o.content ?? '（无内容）' }}
      </p>
    </li>
  </ul>
</template>
