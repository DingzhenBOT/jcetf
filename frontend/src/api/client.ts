// 轻量 fetch 封装：统一基址、错误处理、JSON 解析。
// 不引入 axios（减少依赖）；错误统一抛 ApiError，便于 UI 区分网络/HTTP 状态。

const API_BASE: string = (import.meta.env.VITE_API_BASE as string | undefined) ?? '/api'

export class ApiError extends Error {
  status: number
  body: unknown
  constructor(status: number, message: string, body?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.body = body
  }
}

export function buildQuery(params: Record<string, string | number | undefined | null>): string {
  const usp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') {
      usp.append(k, String(v))
    }
  }
  const s = usp.toString()
  return s ? `?${s}` : ''
}

export async function apiGet<T>(path: string): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${API_BASE}${path}`, {
      headers: { Accept: 'application/json' },
    })
  } catch (e) {
    const msg = e instanceof Error ? e.message : 'unknown network error'
    throw new ApiError(0, `网络请求失败：${msg}`, null)
  }

  if (!res.ok) {
    let msg = `请求失败（HTTP ${res.status}）`
    let body: unknown = null
    try {
      body = await res.json()
      const em = (body as { error?: { message?: string } })?.error?.message
      if (em) msg = em
    } catch {
      // 响应体非 JSON，保留默认文案
    }
    throw new ApiError(res.status, msg, body)
  }

  return (await res.json()) as T
}

export { API_BASE }
