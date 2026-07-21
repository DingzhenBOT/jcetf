// ETF 代码 -> 名称/交易场所 映射（懒加载、进程内缓存）。
// 信号表等只持有 etf_code，需借助 /api/etfs 反查名称与场内/场外，避免重复拉取。
import { reactive } from 'vue'
import { getEtfs } from '@/api/endpoints'

interface CodeInfo {
  name: string
  listing: string | null
}

// 共享单例：首次 ensureEtfNames() 拉取后填充，之后各组件直接读。
const _map = reactive<Record<string, CodeInfo>>({})
let _loaded = false
let _loading: Promise<void> | null = null

export function ensureEtfNames(): Promise<void> {
  if (_loaded) return Promise.resolve()
  if (_loading) return _loading
  _loading = getEtfs()
    .then((list) => {
      for (const e of list) {
        _map[e.etf_code] = {
          name: e.etf_name ?? e.etf_code,
          listing: e.listing ?? null,
        }
      }
      _loaded = true
    })
    .catch(() => {
      // 失败允许后续重试
      _loading = null
    })
  return _loading
}

export function etfName(code: string): string {
  return _map[code]?.name ?? code
}

export function etfListing(code: string): string | null {
  return _map[code]?.listing ?? null
}
