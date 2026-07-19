<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import Card from '@/components/ui/Card.vue'
import StatePanel from '@/components/ui/StatePanel.vue'
import Badge from '@/components/ui/Badge.vue'
import SignalTable from '@/components/sections/SignalTable.vue'
import OpinionList from '@/components/sections/OpinionList.vue'
import { getEtfs, getOpinions, getSignalsHistory } from '@/api/endpoints'
import type { EtfListItem, Opinion, Signal } from '@/api/types'
import { TIER_BADGE, regimeText } from '@/lib/tier'
import { fmtScore, fmtConfidence } from '@/lib/format'
import { toBeijing } from '@/lib/time'

const route = useRoute()
const code = computed(() => String(route.params.code))

const etf = ref<EtfListItem | null>(null)
const opinions = ref<Opinion[]>([])
const history = ref<Signal[]>([])
const loading = ref(false)
const error = ref<string | null>(null)

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const [list, op, hist] = await Promise.all([
      getEtfs(),
      getOpinions(code.value),
      getSignalsHistory({ etf_code: code.value, limit: 20 }),
    ])
    etf.value = list.find((e) => e.etf_code === code.value) ?? null
    opinions.value = op.items
    history.value = hist.items
  } catch (e) {
    error.value = e instanceof Error ? e.message : '未知错误'
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(code, load)

const missingRules = computed(() =>
  etf.value?.latest_signal?.failed_rules?.filter((r) => r.includes('missing')) ?? [],
)
</script>

<template>
  <div class="space-y-5">
    <StatePanel :loading="loading" :error="error" @retry="load">
      <template v-if="etf">
        <!-- 头部 -->
        <div class="flex items-center justify-between flex-wrap gap-3 mb-4">
          <div class="min-w-0">
            <div class="flex items-center gap-2 flex-wrap">
              <h1 class="text-xl font-semibold tracking-tight text-slate-800">{{ etf.etf_code }}</h1>
              <span class="text-slate-500">{{ etf.etf_name ?? '' }}</span>
              <span
                v-if="etf.category"
                class="text-xs px-2 py-0.5 rounded-full bg-slate-100 text-slate-500"
              >
                {{ etf.category }}
              </span>
            </div>
            <p v-if="etf.related_index_code" class="text-xs text-slate-400 mt-1">
              关联指数：{{ etf.related_index_code }} · 关联板块：{{
                (etf.related_sector_codes ?? []).join(', ') || '—'
              }}
            </p>
          </div>
          <div v-if="etf.latest_signal">
            <Badge
              :text="etf.latest_signal.signal_type_text"
              :class="TIER_BADGE[etf.latest_signal.signal_type]"
            />
          </div>
        </div>

        <!-- 最新信号 -->
        <Card
          v-if="etf.latest_signal"
          title="最新信号"
          :subtitle="`生成于 ${toBeijing(etf.latest_signal.generated_at)}`"
        >
          <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
            <div>
              <div class="text-slate-400 text-xs">综合分</div>
              <div class="tnum font-semibold">{{ fmtScore(etf.latest_signal.score) }}</div>
            </div>
            <div>
              <div class="text-slate-400 text-xs">置信度</div>
              <div class="tnum font-semibold">{{ fmtConfidence(etf.latest_signal.confidence) }}</div>
            </div>
            <div>
              <div class="text-slate-400 text-xs">市场环境</div>
              <div class="font-semibold">{{ regimeText(etf.latest_signal.market_regime) }}</div>
            </div>
            <div>
              <div class="text-slate-400 text-xs">建议仓位</div>
              <div class="font-semibold text-slate-700">{{ etf.latest_signal.position_text }}</div>
            </div>
          </div>
          <div v-if="etf.latest_signal.suggested_action" class="mt-3 text-sm text-slate-600">
            {{ etf.latest_signal.suggested_action }}
          </div>
          <!-- 数据不足提示 -->
          <div
            v-if="missingRules.length"
            class="mt-3 text-xs text-amber-600 bg-amber-50 border border-amber-200 rounded-md px-3 py-2"
          >
            部分数据缺失（{{ missingRules.join('、') }}），当前为观察期数据，信号置信度已降级。
          </div>
        </Card>
        <div v-else class="text-sm text-slate-400 py-4">该 ETF 暂无信号。</div>

        <!-- 意见 -->
        <Card class="mt-4" :title="`盘中 / 复盘意见（${opinions.length}）`">
          <StatePanel :loading="false" :error="null" :empty="opinions.length === 0" empty-text="暂无意见">
            <OpinionList :opinions="opinions" />
          </StatePanel>
        </Card>

        <!-- 历史信号 -->
        <Card class="mt-4" :title="`历史信号（${history.length}）`">
          <StatePanel :loading="false" :error="null" :empty="history.length === 0" empty-text="暂无历史信号">
            <SignalTable :signals="history" />
          </StatePanel>
        </Card>
      </template>
      <div v-else class="text-sm text-slate-400 text-center py-10">
        未找到该 ETF（代码：{{ code }}）
      </div>
    </StatePanel>
  </div>
</template>
