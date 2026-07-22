// 6 个已落地端点的类型化封装（P4）+ P6 持仓分析。对应 backend/app/api/routers/*.py。
import { apiGet, apiPost, buildQuery } from './client'
import type {
  Breadth,
  EtfListItem,
  IndexHistory,
  MarketOverview,
  Opinion,
  OpinionsForEtf,
  PortfolioAnalyzeResponse,
  PortfolioPosition,
  Signal,
  SignalHistoryPage,
} from './types'

// GET /api/market/overview —— 总览（前端每 30s 轮询）
export function getOverview(): Promise<MarketOverview> {
  return apiGet<MarketOverview>('/market/overview')
}

// GET /api/market/breadth/latest —— 最新市场宽度
export function getBreadthLatest(): Promise<Breadth> {
  return apiGet<Breadth>('/market/breadth/latest')
}

// GET /api/signals/latest —— 每支生效 ETF 最新信号（前端每 30s 轮询）
export function getSignalsLatest(): Promise<Signal[]> {
  return apiGet<Signal[]>('/signals/latest')
}

// GET /api/signals/history —— 历史信号（etf_code / trading_date / 分页）
export function getSignalsHistory(params: {
  etf_code?: string
  trading_date?: string
  limit?: number
  offset?: number
}): Promise<SignalHistoryPage> {
  return apiGet<SignalHistoryPage>(`/signals/history${buildQuery(params)}`)
}

// GET /api/etfs —— ETF 列表（含最新信号摘要）
export function getEtfs(): Promise<EtfListItem[]> {
  return apiGet<EtfListItem[]>('/etfs')
}

// GET /api/market/index/{code}/history —— 指数日线历史 + 人话自解读
export function getIndexHistory(code: string, days?: number): Promise<IndexHistory> {
  return apiGet<IndexHistory>(`/market/index/${encodeURIComponent(code)}/history${buildQuery({ days })}`)
}

// GET /api/opinions/{etf} —— 某 ETF 全部意见（可选 phase 过滤）
export function getOpinions(etf: string, phase?: string): Promise<OpinionsForEtf> {
  return apiGet<OpinionsForEtf>(`/opinions/${encodeURIComponent(etf)}${buildQuery({ phase })}`)
}

// POST /api/portfolio/analyze —— 提交持仓即时计算（无状态，不落库）
export function analyzePortfolio(positions: PortfolioPosition[]): Promise<PortfolioAnalyzeResponse> {
  return apiPost<PortfolioAnalyzeResponse>('/portfolio/analyze', { positions })
}
