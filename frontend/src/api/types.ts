// P5 API 类型定义 —— 严格镜像后端 P4 Pydantic schema（backend/app/api/schemas.py）。
// 字段名、可空性（? | null）1:1 对齐，避免前端渲染时访问 undefined。

// 公共信号档位英文码（DESIGN §9.4 / opinion_engine.templates.TIER_TEXT）
export type SignalType =
  | 'NO_PARTICIPATE'
  | 'OBSERVE'
  | 'SMALL_POSITION'
  | 'OPPORTUNITY_ENHANCE'
  | 'NO_CHASE_HIGH'
  | 'MARKET_RISK_HIGH'

// 市场状态（signal.market_regime）
export type MarketRegime =
  | 'STRONG_UP'
  | 'TREND_UP'
  | 'VOLATILE'
  | 'WEAK'
  | 'BEAR'
  | string

// 意见阶段（opinion.phase）
export type OpinionPhase =
  | 'pre_market'
  | 'midday'
  | 'pre_close'
  | 'post_close'
  | null

export interface IndexSnapshot {
  code: string
  name: string
  close?: number | null
  change_percent?: number | null
  source?: string | null
}

export interface IndexHistoryPoint {
  date: string
  close: number
  volume: number
  amount: number
  change_percent?: number | null
}

export interface IndexHistory {
  code: string
  name: string
  points: IndexHistoryPoint[]
  read: string
  signals: string[]
}

// ETF 日线历史与指数历史同形，直接复用 IndexHistory
export type EtfHistory = IndexHistory

// 盘中 1 分钟分时（类似同花顺分时图）
export interface IntradayPoint {
  time: string // 北京时间 ISO（后端已转回北京时间）
  price: number // 该分钟收盘价
  avg: number // 均价（累计成交额 / 累计成交量）
  volume: number // 该分钟成交量
}

export interface Intraday {
  code: string
  name: string
  date: string // 交易日 YYYY-MM-DD
  prev_close: number | null // 昨收（用于着色与涨跌幅）
  points: IntradayPoint[]
  read: string
  signals: string[]
}

export interface Breadth {
  trading_date?: string | null
  total_rise?: number | null
  total_fall?: number | null
  total_flat?: number | null
  limit_up?: number | null
  limit_down?: number | null
  total_amount?: number | null
  advance_ratio?: number | null
  data_source?: string | null
}

export interface SignalRisk {
  counts: Record<string, number>
  total: number
  market_risk_level: string
}

export interface MarketOverview {
  as_of?: string | null
  indices: IndexSnapshot[]
  breadth: Breadth | null
  signal_risk: SignalRisk
}

export interface Signal {
  signal_id: string
  strategy_version: string
  generated_at: string
  trading_date: string
  target_etf: string
  signal_type: SignalType
  signal_type_text: string
  score?: number | null
  confidence?: number | null
  market_regime?: MarketRegime | null
  suggested_action?: string | null
  suggested_position_range?: number[] | null
  position_text: string
  one_liner?: string | null
  supporting_metrics?: Record<string, unknown> | null
  risk_flags?: Record<string, unknown> | null
  triggered_rules?: string[] | null
  failed_rules?: string[] | null
  invalidation_conditions?: Record<string, unknown> | null
  review_time?: string | null
}

export interface EtfListItem {
  etf_code: string
  etf_name?: string | null
  category?: string | null
  listing?: string | null // '场内' / '场外'
  related_sector_codes?: string[] | null
  related_index_code?: string | null
  latest_signal: Signal | null
}

export interface SignalHistoryPage {
  items: Signal[]
  total: number
  limit: number
  offset: number
}

export interface Opinion {
  opinion_id: string
  signal_id?: string | null
  generated_at: string
  trading_date: string
  phase?: OpinionPhase
  title?: string | null
  content?: string | null
  input_summary?: Record<string, unknown> | null
  template_version?: string | null
}

export interface OpinionsForEtf {
  etf_code: string
  items: Opinion[]
}

// ---- P6：按需持仓分析（无状态，默认不落库） ----
// 动作码（DESIGN §9.5）
export type PortfolioAction = 'HOLD' | 'REDUCE' | 'EXIT' | 'RECONFIRM'

export interface PortfolioPosition {
  etf_code: string
  cost_price: number
  position_percent: number
  quantity?: number | null
}

export interface PortfolioAnalyzeRequest {
  positions: PortfolioPosition[]
}

export interface PortfolioAnalyzeItem {
  etf_code: string
  action: PortfolioAction
  reason: string
  risk: string
  return_percent?: number | null
  pnl_amount?: number | null
  suggested_position_text?: string | null
  suggested_position_range?: number[] | null
  invalidation_conditions: string[]
  review_time?: string | null
}

export interface PortfolioAnalyzeResponse {
  items: PortfolioAnalyzeItem[]
}

// 通用列表查询状态（Loading / Empty / Error）
export interface FetchState<T> {
  data: T
  loading: boolean
  error: string | null
  loaded: boolean
}
