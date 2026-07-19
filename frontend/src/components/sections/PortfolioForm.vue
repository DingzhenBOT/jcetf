<script setup lang="ts">
import { reactive, ref } from 'vue'
import type { PortfolioPosition } from '@/api/types'

const props = defineProps<{ initialEtf?: string }>()
const emit = defineEmits<{ analyze: [positions: PortfolioPosition[]] }>()

interface Row {
  etf_code: string
  cost_price: string
  position_percent: string
  quantity: string
}

function blank(): Row {
  return { etf_code: '', cost_price: '', position_percent: '', quantity: '' }
}

const rows = reactive<Row[]>(
  props.initialEtf ? [{ ...blank(), etf_code: props.initialEtf }] : [blank()],
)
const errors = ref<string[]>([])

function addRow() {
  if (rows.length < 20) rows.push(blank())
}
function removeRow(i: number) {
  rows.splice(i, 1)
  if (rows.length === 0) rows.push(blank())
}

function submit() {
  const errs: string[] = []
  const positions: PortfolioPosition[] = []
  const seen = new Set<string>()
  let sum = 0
  rows.forEach((r, i) => {
    const n = i + 1
    const code = r.etf_code.trim()
    if (!code) {
      errs.push(`第 ${n} 行：ETF 代码不能为空`)
      return
    }
    if (seen.has(code)) {
      errs.push(`第 ${n} 行：ETF ${code} 重复`)
      return
    }
    seen.add(code)
    const cost = Number(r.cost_price)
    if (!isFinite(cost) || cost <= 0) {
      errs.push(`第 ${n} 行：成本价需大于 0`)
      return
    }
    const pct = Number(r.position_percent)
    if (!isFinite(pct) || pct < 0 || pct > 100) {
      errs.push(`第 ${n} 行：仓位百分比需在 0–100`)
      return
    }
    sum += pct
    const q = r.quantity.trim()
    const qty = q === '' ? null : Number(q)
    if (qty !== null && (!isFinite(qty) || qty <= 0)) {
      errs.push(`第 ${n} 行：数量需为正数`)
      return
    }
    positions.push({ etf_code: code, cost_price: cost, position_percent: pct, quantity: qty })
  })
  if (positions.length > 20) errs.push('最多 20 只 ETF')
  if (sum > 100) errs.push(`仓位合计 ${sum.toFixed(1)}% 已超过 100%`)
  if (positions.length === 0) errs.push('请至少填写一只 ETF')
  errors.value = errs
  if (errs.length === 0 && positions.length > 0) emit('analyze', positions)
}
</script>

<template>
  <div class="bg-white border border-slate-200 rounded-xl shadow-sm">
    <div class="px-4 py-3 border-b border-slate-100">
      <h2 class="text-sm font-semibold text-slate-700">持仓录入</h2>
      <p class="text-xs text-slate-400 mt-0.5">
        填写成本与仓位，提交后即时分析（不保存，仅当前计算）。最多 20 只，仓位合计 ≤ 100%。
      </p>
    </div>

    <div class="p-4">
      <!-- 表头 -->
      <div class="grid grid-cols-[1.4fr_1fr_1fr_1fr_auto] gap-2 pb-2 text-xs text-slate-400">
        <div>ETF 代码</div>
        <div>成本价</div>
        <div>仓位 %</div>
        <div>数量（可选）</div>
        <div></div>
      </div>

      <div class="space-y-2">
        <div
          v-for="(r, i) in rows"
          :key="i"
          class="grid grid-cols-[1.4fr_1fr_1fr_1fr_auto] gap-2 items-center"
        >
          <input
            v-model="r.etf_code"
            type="text"
            placeholder="如 510300"
            class="w-full px-2 py-1.5 text-sm border border-slate-200 rounded-md focus:outline-none focus:ring-2 focus:ring-slate-300"
          />
          <input
            v-model="r.cost_price"
            type="number"
            step="0.001"
            min="0"
            placeholder="0.000"
            class="w-full px-2 py-1.5 text-sm border border-slate-200 rounded-md focus:outline-none focus:ring-2 focus:ring-slate-300 tnum"
          />
          <input
            v-model="r.position_percent"
            type="number"
            step="1"
            min="0"
            max="100"
            placeholder="0"
            class="w-full px-2 py-1.5 text-sm border border-slate-200 rounded-md focus:outline-none focus:ring-2 focus:ring-slate-300 tnum"
          />
          <input
            v-model="r.quantity"
            type="number"
            step="1"
            min="0"
            placeholder="可选"
            class="w-full px-2 py-1.5 text-sm border border-slate-200 rounded-md focus:outline-none focus:ring-2 focus:ring-slate-300 tnum"
          />
          <button
            type="button"
            class="px-2 py-1.5 text-slate-400 hover:text-rose-500 text-sm"
            title="删除该行"
            @click="removeRow(i)"
          >
            删除
          </button>
        </div>
      </div>

      <div class="mt-3 flex items-center gap-3">
        <button
          type="button"
          class="px-3 py-1.5 text-sm rounded-md border border-slate-200 text-slate-600 hover:bg-slate-50 disabled:opacity-40"
          :disabled="rows.length >= 20"
          @click="addRow"
        >
          + 增加一行
        </button>
        <button
          type="button"
          class="px-4 py-1.5 text-sm rounded-md bg-slate-800 text-white hover:bg-slate-900 font-medium"
          @click="submit"
        >
          分析持仓
        </button>
        <span class="text-xs text-slate-400">{{ rows.length }} / 20</span>
      </div>

      <ul v-if="errors.length" class="mt-3 space-y-1 text-xs text-rose-600">
        <li v-for="(e, i) in errors" :key="i">· {{ e }}</li>
      </ul>
    </div>
  </div>
</template>
