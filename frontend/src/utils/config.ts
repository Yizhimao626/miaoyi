/**
 * 喵艺 · 统一后端地址配置模块
 * ------------------------------------------------------------
 * 集中管理 API / WebSocket 的后端地址解析，避免各文件各自硬编码：
 *
 * 解析优先级（运行时动态读取，改设置无需重新构建）：
 *   1. localStorage.miaoyi_api_base —— 用户在 App 设置里配置的服务器地址（最高优先）
 *   2. import.meta.env.VITE_API_BASE —— 构建时注入（vite build 使用 .env.production）
 *   3. /api —— 开发环境（经 Vite 代理）回退
 *
 * WebSocket 地址由 API 地址派生（http→ws、https→wss），保证两者始终指向同一后端。
 * 【历史教训】若 API 与 WS 分开配置，用户只改 API 地址会导致 WS 仍连旧地址，
 * 表现为"消息发出去没回复"。本模块统一派生，杜绝该问题。
 */
const CUSTOM_API_BASE_KEY = 'miaoyi_api_base'

export function getBaseUrl(): string {
  try {
    const custom = localStorage.getItem(CUSTOM_API_BASE_KEY)
    if (custom && /^https?:\/\//.test(custom)) {
      return custom.replace(/\/+$/, '')
    }
  } catch { /* localStorage 不可用时忽略 */ }
  return import.meta.env.VITE_API_BASE || '/api'
}

export function setBaseUrl(url: string): void {
  try {
    localStorage.setItem(CUSTOM_API_BASE_KEY, url.trim())
  } catch { /* ignore */ }
}

/** 清除自定义地址，回退到构建时注入的默认地址 */
export function resetBaseUrl(): void {
  try {
    localStorage.removeItem(CUSTOM_API_BASE_KEY)
  } catch { /* ignore */ }
}

/** 是否使用"默认地址"（未配置自定义服务器） */
export function isDefaultBaseUrl(): boolean {
  return getBaseUrl() === '/api'
}

/** 派生 WebSocket 地址：http→ws、https→wss，同一后端 */
export function getWsUrl(): string {
  try {
    const base = getBaseUrl()
    if (/^https?:\/\//.test(base)) {
      // 【云服务器适配 2026-10-01】REST 挂载在 /api/*，WS 挂载在根 /ws：
      // API 基地址若以 /api 结尾，派生 WS 地址前先剥掉，避免拼出 /api/ws 404
      const wsBase = base.replace(/\/api\/?$/, '')
      return `${wsBase.replace(/^http/, 'ws')}/ws`
    }
  } catch { /* ignore */ }
  const envUrl = import.meta.env.VITE_WS_URL
  if (envUrl) return envUrl
  return '/ws'
}
