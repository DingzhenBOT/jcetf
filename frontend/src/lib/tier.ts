// 档位 / 状态 中文映射与配色 —— 与后端 opinion_engine.templates.TIER_TEXT 严格一致。
// 注意：A股惯例「红涨绿跌」，故 SMALL_POSITION/OPPORTUNITY_ENHANCE 用绿系，
//      NO_CHASE_HIGH/MARKET_RISK_HIGH 用橙/玫红警示。配色为 Tailwind 静态类名，勿动态拼接。
import type { PortfolioAction, SignalType } from '@/api/types'

// 持仓分析动作（DESIGN §9.5）：继续持有 / 降低仓位 / 触发退出条件 / 等待重新确认
export const ACTION_TEXT: Record<PortfolioAction, string> = {
  HOLD: '继续持有',
  REDUCE: '降低仓位',
  EXIT: '触发退出',
  RECONFIRM: '等待确认',
}

// 动作徽标配色（完整类，勿动态拼接）
export const ACTION_BADGE: Record<PortfolioAction, string> = {
  HOLD: 'bg-emerald-100 text-emerald-700 border-emerald-200',
  REDUCE: 'bg-amber-100 text-amber-700 border-amber-200',
  EXIT: 'bg-rose-100 text-rose-700 border-rose-200',
  RECONFIRM: 'bg-slate-100 text-slate-600 border-slate-200',
}

export const TIER_TEXT: Record<SignalType, string> = {
  NO_PARTICIPATE: '暂不参与',
  OBSERVE: '加入观察',
  SMALL_POSITION: '允许小仓位试错',
  OPPORTUNITY_ENHANCE: '机会增强',
  NO_CHASE_HIGH: '禁止追高',
  MARKET_RISK_HIGH: '市场风险较高',
}

// 档位积极度（用于排序：越积极越靠前）
export const TIER_ORDER: Record<SignalType, number> = {
  OPPORTUNITY_ENHANCE: 5,
  SMALL_POSITION: 4,
  OBSERVE: 3,
  NO_PARTICIPATE: 2,
  MARKET_RISK_HIGH: 1,
  NO_CHASE_HIGH: 0,
}

// 档位徽标配色（完整 bg/text/border，避免与默认色冲突）
export const TIER_BADGE: Record<SignalType, string> = {
  NO_PARTICIPATE: 'bg-slate-100 text-slate-600 border-slate-200',
  OBSERVE: 'bg-sky-100 text-sky-700 border-sky-200',
  SMALL_POSITION: 'bg-emerald-100 text-emerald-700 border-emerald-200',
  OPPORTUNITY_ENHANCE: 'bg-green-100 text-green-700 border-green-200',
  NO_CHASE_HIGH: 'bg-amber-100 text-amber-700 border-amber-200',
  MARKET_RISK_HIGH: 'bg-rose-100 text-rose-700 border-rose-200',
}

// 档位对应的 ECharts 色（饼图/柱状）
export const TIER_COLOR: Record<SignalType, string> = {
  NO_PARTICIPATE: '#94a3b8',
  OBSERVE: '#0ea5e9',
  SMALL_POSITION: '#10b981',
  OPPORTUNITY_ENHANCE: '#16a34a',
  NO_CHASE_HIGH: '#f59e0b',
  MARKET_RISK_HIGH: '#e11d48',
}

// 档位对应的左侧强调边框色（结论 Hero 用；border-l-* 仅染左侧，卡片其余边保持中性）
export const TIER_BORDER: Record<SignalType, string> = {
  NO_PARTICIPATE: 'border-l-slate-300',
  OBSERVE: 'border-l-sky-300',
  SMALL_POSITION: 'border-l-emerald-300',
  OPPORTUNITY_ENHANCE: 'border-l-green-300',
  NO_CHASE_HIGH: 'border-l-amber-300',
  MARKET_RISK_HIGH: 'border-l-rose-300',
}

export const REGIME_TEXT: Record<string, string> = {
  STRONG_UP: '强势上行',
  TREND_UP: '震荡上行',
  VOLATILE: '高波动',
  WEAK: '偏弱',
  BEAR: '空头',
}

export function regimeText(r: string | null | undefined): string {
  if (!r) return '未知'
  return REGIME_TEXT[r] ?? r
}

export const PHASE_TEXT: Record<string, string> = {
  pre_market: '盘前',
  midday: '午间',
  pre_close: '收盘前',
  post_close: '收盘复盘',
}

export function phaseText(p: string | null | undefined): string {
  if (!p) return '盘中'
  return PHASE_TEXT[p] ?? p
}

// 市场风险等级配色
const RISK_LEVEL_BADGE: Record<string, string> = {
  偏低: 'bg-emerald-100 text-emerald-700 border-emerald-200',
  中性: 'bg-sky-100 text-sky-700 border-sky-200',
  偏高: 'bg-amber-100 text-amber-700 border-amber-200',
  高: 'bg-rose-100 text-rose-700 border-rose-200',
  未知: 'bg-slate-100 text-slate-600 border-slate-200',
}

export function riskLevelBadge(level: string): string {
  return RISK_LEVEL_BADGE[level] ?? RISK_LEVEL_BADGE['未知']
}

// 场内 / 场外 徽标配色（ETF 交易场所区分）
const LISTING_BADGE: Record<string, string> = {
  场内: 'bg-indigo-100 text-indigo-700 border-indigo-200',
  场外: 'bg-teal-100 text-teal-700 border-teal-200',
}

export function listingBadge(listing: string | null | undefined): string {
  if (!listing) return 'bg-slate-100 text-slate-500 border-slate-200'
  return LISTING_BADGE[listing] ?? 'bg-slate-100 text-slate-500 border-slate-200'
}
