<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { getNews } from '@/api/endpoints'
import type { NewsItem } from '@/api/types'

const items = ref<NewsItem[]>([])
const loading = ref(false)
const error = ref<string | null>(null)

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const r = await getNews(30)
    items.value = r.items ?? []
  } catch (e) {
    error.value = e instanceof Error ? e.message : '未知错误'
  } finally {
    loading.value = false
  }
}
onMounted(load)

function hhmm(t: string): string {
  // showTime 形如 2026-07-24 14:30:00 -> 取 HH:MM
  const m = /(\d{2}:\d{2})/.exec(t)
  return m ? m[1] : t
}
</script>

<template>
  <div class="flex items-center gap-3 overflow-hidden">
    <span class="shrink-0 text-xs font-medium text-slate-500 px-2 py-1 rounded bg-slate-100">实时资讯</span>
    <div class="flex items-center gap-5 overflow-x-auto whitespace-nowrap no-scrollbar">
      <template v-if="loading">
        <span class="text-xs text-slate-400">加载中…</span>
      </template>
      <template v-else-if="error">
        <span class="text-xs text-amber-600">{{ error }}</span>
      </template>
      <template v-else-if="items.length">
        <a
          v-for="(n, i) in items.slice(0, 24)"
          :key="i"
          class="text-sm text-slate-600 hover:text-slate-900 shrink-0"
          :title="n.summary || n.title"
        >
          <span class="text-slate-400 tnum mr-1.5">{{ hhmm(n.time) }}</span>{{ n.title }}
        </a>
      </template>
      <span v-else class="text-xs text-slate-400">暂无资讯</span>
    </div>
  </div>
</template>

<style scoped>
.no-scrollbar::-webkit-scrollbar {
  display: none;
}
.no-scrollbar {
  -ms-overflow-style: none;
  scrollbar-width: none;
}
</style>
