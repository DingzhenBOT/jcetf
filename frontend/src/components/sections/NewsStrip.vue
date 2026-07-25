<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { getNews } from '@/api/endpoints'
import type { NewsItem } from '@/api/types'
import { analyzeNewsImpact } from '@/lib/newsImpact'
import Modal from '@/components/ui/Modal.vue'

const items = ref<NewsItem[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const selected = ref<NewsItem | null>(null)

// 「最热」启发式：以时效性为主，叠加突发/政策关键词加权，取前 5。
const HOT_WORDS = [
  '突发', '重磅', '刚刚', '快讯', '央行', '证监会', '美联储', '降准', '降息',
  '印花税', '利好', '利空', '大涨', '大跌', '暴涨', '暴跌', '涨停', '跌停',
  '回购', '减持', '增持', '中标', '爆雷', '调查', '立案',
]
function hotBoost(n: NewsItem): number {
  const t = `${n.title}${n.summary}`
  return HOT_WORDS.filter((w) => t.includes(w)).length
}
const hot5 = computed<NewsItem[]>(() =>
  [...items.value].sort((a, b) => hotBoost(b) - hotBoost(a)).slice(0, 5),
)
// 跑马灯需复制一份首尾衔接
const loopItems = computed<NewsItem[]>(() => [...hot5.value, ...hot5.value])

const impact = computed(() =>
  selected.value ? analyzeNewsImpact(selected.value.title, selected.value.summary) : null,
)

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

function hhmm(t: string): string {
  const m = /(\d{2}:\d{2})/.exec(t)
  return m ? m[1] : t
}
function openItem(n: NewsItem): void {
  selected.value = n
}

onMounted(load)
// 跟随首页 60s 轮询节奏刷新
const timer = window.setInterval(load, 60_000)
onBeforeUnmount(() => window.clearInterval(timer))
</script>

<template>
  <div class="flex items-center gap-3 overflow-hidden">
    <span class="shrink-0 text-xs font-medium text-slate-500 px-2 py-1 rounded bg-slate-100">实时资讯</span>

    <!-- 自动滚动跑马灯（hover 暂停，便于点击） -->
    <div class="relative flex-1 overflow-hidden">
      <div v-if="loading" class="text-xs text-slate-400 py-1">加载中…</div>
      <div v-else-if="error" class="text-xs text-amber-600 py-1">{{ error }}</div>
      <div
        v-else-if="hot5.length"
        class="marquee-track flex items-center gap-8 whitespace-nowrap will-change-transform hover:[animation-play-state:paused]"
      >
        <button
          v-for="(n, i) in loopItems"
          :key="i"
          type="button"
          class="text-sm text-slate-600 hover:text-slate-900 shrink-0 text-left"
          :title="`${n.title}（点击查看摘要与影响）`"
          @click="openItem(n)"
        >
          <span class="text-slate-400 tnum mr-1.5">{{ hhmm(n.time) }}</span>{{ n.title }}
        </button>
      </div>
      <div v-else class="text-xs text-slate-400 py-1">暂无资讯</div>
    </div>

    <!-- 点击弹窗：摘要 + 规则模板影响分析 -->
    <Modal :open="selected !== null" :title="selected?.title ?? ''" @close="selected = null">
      <template v-if="selected">
        <div class="text-xs text-slate-400 mb-2">{{ selected.time }}</div>
        <p class="text-sm leading-relaxed text-slate-700">{{ selected.summary || '（暂无摘要）' }}</p>

        <div v-if="impact" class="mt-4 rounded-lg border border-slate-100 bg-slate-50 p-3">
          <div class="flex items-center gap-2 mb-1.5">
            <span class="text-xs font-medium text-slate-500">对板块 / 大盘的影响</span>
            <span
              class="text-xs px-2 py-0.5 rounded-full"
              :class="{
                'bg-emerald-50 text-emerald-600': impact.sentiment === '利好',
                'bg-rose-50 text-rose-600': impact.sentiment === '利空',
                'bg-slate-100 text-slate-500': impact.sentiment === '中性',
              }"
            >
              {{ impact.sentiment }}
            </span>
          </div>
          <p class="text-sm leading-relaxed text-slate-700">{{ impact.text }}</p>
          <div v-if="impact.sectors.length" class="mt-2 flex flex-wrap gap-1.5">
            <span
              v-for="s in impact.sectors"
              :key="s"
              class="rounded-full bg-sky-50 px-2 py-0.5 text-xs text-sky-600"
            >{{ s }}</span>
          </div>
        </div>
      </template>
    </Modal>
  </div>
</template>

<style scoped>
.marquee-track {
  animation: marquee 32s linear infinite;
}
@keyframes marquee {
  from {
    transform: translateX(0);
  }
  to {
    transform: translateX(-50%);
  }
}
</style>
