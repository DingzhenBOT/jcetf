// 全局市场数据 store —— 负责 DESIGN 指定的「轮询」：
//   GET /api/market/overview + GET /api/signals/latest
// 状态在 App.vue 挂载时 startPolling() 启动，卸载时 stopPolling() 停止。
// 各页面按需自行拉取 /api/etfs、/api/opinions/{etf} 等，并监听 lastUpdated 以随轮询刷新。
//
// 注意：这是「增量数据轮询」，并非整页刷新——页面不会重载、滚动位置不被重置。
// 另有一个独立的 1 秒「时钟」(_now) 仅用于驱动顶部的「还 X 秒刷新」倒数显示。
import { reactive, ref, computed } from 'vue'
import { getOverview, getSignalsLatest } from '@/api/endpoints'
import type { MarketOverview, Signal } from '@/api/types'

// 轮询间隔：之前 30 秒偏频繁，改为 60 秒（数据本身分钟级变化，无需更密）。
export const POLL_INTERVAL_MS = 60_000

interface MarketState {
  overview: MarketOverview | null
  latestSignals: Signal[]
  loading: boolean
  error: string | null
  lastUpdated: string | null // 本次成功拉取时间（ISO）
  connected: boolean
}

const _state = reactive<MarketState>({
  overview: null,
  latestSignals: [],
  loading: false,
  error: null,
  lastUpdated: null,
  connected: false,
})

// 1 秒时钟：仅用于实时倒数显示，不影响数据拉取频率。
const _now = ref(Date.now())

let timer: ReturnType<typeof setInterval> | null = null
let clock: ReturnType<typeof setInterval> | null = null

async function tick(): Promise<void> {
  _state.loading = true
  _state.error = null
  try {
    const [ov, sigs] = await Promise.all([getOverview(), getSignalsLatest()])
    _state.overview = ov
    _state.latestSignals = sigs
    _state.connected = true
    _state.lastUpdated = new Date().toISOString()
  } catch (e) {
    _state.connected = false
    _state.error = e instanceof Error ? e.message : '未知错误'
  } finally {
    _state.loading = false
  }
}

// 距离下次自动刷新的剩余秒数（基于上次成功拉取时间 + 轮询间隔）。
// 读取 _now 与 lastUpdated，二者均为响应式依赖，故每秒自动倒数。
export const secondsToRefresh = computed<number>(() => {
  if (!_state.lastUpdated) return Math.ceil(POLL_INTERVAL_MS / 1000)
  const elapsed = _now.value - new Date(_state.lastUpdated).getTime()
  return Math.max(0, Math.ceil((POLL_INTERVAL_MS - elapsed) / 1000))
})

export function startPolling(intervalMs = POLL_INTERVAL_MS): void {
  if (timer !== null) return
  void tick()
  timer = setInterval(() => void tick(), intervalMs)
  // 时钟独立于数据轮询，仅在首个轮询启动时开一次。
  if (clock === null) {
    clock = setInterval(() => {
      _now.value = Date.now()
    }, 1000)
  }
}

export function stopPolling(): void {
  if (timer !== null) {
    clearInterval(timer)
    timer = null
  }
  if (clock !== null) {
    clearInterval(clock)
    clock = null
  }
}

export function refreshNow(): Promise<void> {
  return tick()
}

// 直接导出 reactive 状态（组件只读使用；约定不在此处直接改写，须经上述函数）。
export const marketState = _state
