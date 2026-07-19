// 全局市场数据 store —— 负责 DESIGN 指定的「每 30 秒轮询」：
//   GET /api/market/overview + GET /api/signals/latest
// 状态在 App.vue 挂载时 startPolling() 启动，卸载时 stopPolling() 停止。
// 各页面按需自行拉取 /api/etfs、/api/opinions/{etf} 等，并监听 lastUpdated 以随轮询刷新。
import { reactive } from 'vue'
import { getOverview, getSignalsLatest } from '@/api/endpoints'
import type { MarketOverview, Signal } from '@/api/types'

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

let timer: ReturnType<typeof setInterval> | null = null

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

export function startPolling(intervalMs = 30_000): void {
  if (timer !== null) return
  void tick()
  timer = setInterval(() => void tick(), intervalMs)
}

export function stopPolling(): void {
  if (timer !== null) {
    clearInterval(timer)
    timer = null
  }
}

export function refreshNow(): Promise<void> {
  return tick()
}

// 直接导出 reactive 状态（组件只读使用；约定不在此处直接改写，须经上述函数）。
export const marketState = _state
