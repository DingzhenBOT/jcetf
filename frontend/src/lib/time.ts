// 时间工具。后端时间字段为 naive UTC ISO（见 backend/app/api/serializers._iso），
// 前端统一按北京时间（UTC+8）展示（DESIGN §0 假设#1）。
function asUtc(iso: string): Date {
  // naive UTC（如 "2026-07-19T06:30:00"）补 Z 视为 UTC 时刻；已带 Z 的保持不变。
  const s = !iso.endsWith('Z') && !iso.includes('+') ? iso + 'Z' : iso
  return new Date(s)
}

export function toBeijing(iso: string | null | undefined): string {
  if (!iso) return '--'
  const d = asUtc(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

export function toBeijingDate(iso: string | null | undefined): string {
  if (!iso) return '--'
  const d = asUtc(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  })
}

// 相对时间（"3 分钟前"），用于「最后更新」
export function toRelative(iso: string | null | undefined): string {
  if (!iso) return '--'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const diff = Date.now() - d.getTime()
  const sec = Math.floor(diff / 1000)
  if (sec < 0) return '刚刚'
  if (sec < 60) return `${sec} 秒前`
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min} 分钟前`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr} 小时前`
  const day = Math.floor(hr / 24)
  return `${day} 天前`
}

// 数据新鲜度：as_of（交易日）距今天数（按北京日期，忽略时分秒）
export function daysSinceBeijingDate(iso: string | null | undefined): number | null {
  if (!iso) return null
  const d = asUtc(iso)
  if (Number.isNaN(d.getTime())) return null
  const now = new Date()
  const beijingNow = new Date(now.toLocaleString('en-US', { timeZone: 'Asia/Shanghai' }))
  const a = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate())
  const b = Date.UTC(beijingNow.getUTCFullYear(), beijingNow.getUTCMonth(), beijingNow.getUTCDate())
  return Math.floor((b - a) / 86_400_000)
}
