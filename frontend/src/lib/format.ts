// 数值格式化工具。所有金额按「元」入参，按「亿/万」展示（A股习惯）。

export function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '--'
  const s = v.toFixed(digits)
  return v > 0 ? `+${s}%` : `${s}%`
}

export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '--'
  return v.toFixed(digits)
}

export function fmtInt(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '--'
  return v.toLocaleString('zh-CN')
}

// 成交额（元 → 亿 / 万）
export function fmtAmountYi(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '--'
  const yi = v / 1e8
  if (Math.abs(yi) >= 1) return `${yi.toFixed(2)} 亿`
  const wan = v / 1e4
  return `${wan.toFixed(2)} 万`
}

export function fmtScore(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '--'
  return v.toFixed(0)
}

export function fmtConfidence(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '--'
  // 后端 confidence 为 0–100 百分比（非 0–1），直接展示
  return `${Math.round(v)}%`
}

// 涨跌颜色（A股：红涨绿跌）。0 / 缺失 → 中性灰。
export function changeColor(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v) || v === 0) return 'text-flat'
  return v > 0 ? 'text-up' : 'text-down'
}
