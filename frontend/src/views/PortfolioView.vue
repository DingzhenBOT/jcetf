<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import PortfolioForm from '@/components/sections/PortfolioForm.vue'
import PortfolioResults from '@/components/sections/PortfolioResults.vue'
import StatePanel from '@/components/ui/StatePanel.vue'
import { analyzePortfolio } from '@/api/endpoints'
import { ApiError } from '@/api/client'
import type { PortfolioAnalyzeItem, PortfolioPosition } from '@/api/types'

const route = useRoute()
const prefillEtf = computed(() => {
  const q = route.query.etf
  return typeof q === 'string' && q.trim() ? q.trim() : undefined
})

const items = ref<PortfolioAnalyzeItem[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const analyzed = ref(false)
const lastPositions = ref<PortfolioPosition[]>([])

async function run(positions: PortfolioPosition[]) {
  lastPositions.value = positions
  loading.value = true
  error.value = null
  try {
    const res = await analyzePortfolio(positions)
    items.value = res.items
    analyzed.value = true
  } catch (e) {
    error.value = e instanceof ApiError || e instanceof Error ? e.message : '分析失败'
  } finally {
    loading.value = false
  }
}

function onAnalyze(positions: PortfolioPosition[]) {
  void run(positions)
}
</script>

<template>
  <div class="space-y-5">
    <div>
      <h1 class="text-xl font-semibold tracking-tight text-slate-800">持仓分析</h1>
      <p class="text-sm text-slate-400 mt-1">
        提交持仓即时计算，<span class="text-slate-500">不保存、不产生用户状态</span>（DESIGN § 按需持仓分析）。
        盈亏需依赖最新行情，无行情时仅给动作与建议仓位。
      </p>
    </div>

    <!-- 表单：prefill 变化时通过 key 重新挂载，预填 ETF 代码 -->
    <PortfolioForm
      :key="prefillEtf ?? 'blank'"
      :initial-etf="prefillEtf"
      @analyze="onAnalyze"
    />

    <StatePanel
      :loading="loading"
      :error="error"
      :empty="analyzed && items.length === 0"
      empty-text="暂无分析结果，请先在上方录入持仓并提交"
      @retry="run(lastPositions)"
    >
      <PortfolioResults :items="items" />
    </StatePanel>
  </div>
</template>
