/**
 * 喵艺 AI 认证模块
 * JWT Token 管理 + Refresh Token 自动刷新
 * 请求拦截器 + 安全存储
 */

const TOKEN_KEY = 'miaoyi_access_token'
const REFRESH_TOKEN_KEY = 'miaoyi_refresh_token'
const TOKEN_EXPIRE_KEY = 'miaoyi_token_expire'

import { getBaseUrl } from './config'

// 正在进行的刷新请求（用于并发请求时复用）
let isRefreshing = false
let refreshSubscribers: Array<(token: string | null) => void> = []

/** 解码 JWT Token */
export function decodeJWT<T = any>(token: string): T | null {
  try {
    const parts = token.split('.')
    if (parts.length !== 3) return null
    // base64url → base64，再按 UTF-8 解码：atob 直出 latin 串，
    // payload 含中文（如用户名）时 JSON.parse 会得到乱码甚至抛错
    const b64 = parts[1].replace(/-/g, '+').replace(/_/g, '/')
    const binary = atob(b64)
    const bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0))
    const payload = new TextDecoder('utf-8').decode(bytes)
    return JSON.parse(payload) as T
  } catch {
    return null
  }
}

/** 获取 Access Token */
export function getAccessToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

/** 获取 Refresh Token */
export function getRefreshToken(): string | null {
  try {
    return localStorage.getItem(REFRESH_TOKEN_KEY)
  } catch {
    return null
  }
}

/** 设置 Token */
export function setTokens(accessToken: string, newRefreshToken?: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, accessToken)
    
    // 解码并保存过期时间
    const payload = decodeJWT<{ exp?: number }>(accessToken)
    if (payload?.exp) {
      localStorage.setItem(TOKEN_EXPIRE_KEY, String(payload.exp * 1000))
    }
    
    if (newRefreshToken) {
      localStorage.setItem(REFRESH_TOKEN_KEY, newRefreshToken)
    }
  } catch {
    // localStorage 可能被禁用
  }
}

/** 清除所有 Token */
export function clearTokens(): void {
  try {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(REFRESH_TOKEN_KEY)
    localStorage.removeItem(TOKEN_EXPIRE_KEY)
  } catch {
    // 忽略错误
  }
}

/** 检查 Token 是否即将过期 (5分钟内) */
export function isTokenExpiringSoon(): boolean {
  try {
    const expireStr = localStorage.getItem(TOKEN_EXPIRE_KEY)
    if (!expireStr) return false
    const expireTime = parseInt(expireStr, 10)
    const now = Date.now()
    return now >= expireTime - 5 * 60 * 1000
  } catch {
    return false
  }
}

/** 检查 Token 是否已过期 */
export function isTokenExpired(): boolean {
  try {
    const expireStr = localStorage.getItem(TOKEN_EXPIRE_KEY)
    if (!expireStr) return true
    return Date.now() >= parseInt(expireStr, 10)
  } catch {
    return true
  }
}

/** 刷新 Token - 主函数 */
export async function refreshAuthToken(): Promise<string | null> {
  // 【轮换兼容·单飞】并发的刷新调用复用同一个请求。后端已启用 Refresh Token 轮换，
  // 旧刷新令牌一经使用即失效；若两个请求并发用同一旧令牌刷新，必有一个失败并把用户踢下线。
  if (inflightRefresh) return inflightRefresh
  inflightRefresh = doRefreshToken().finally(() => { inflightRefresh = null })
  return inflightRefresh
}

/** 进行中的刷新请求（单飞锁） */
let inflightRefresh: Promise<string | null> | null = null

/** 实际执行刷新（请勿直接调用，走 refreshAuthToken 以获得单飞保护） */
async function doRefreshToken(): Promise<string | null> {
  const storedRefreshToken = getRefreshToken()
  if (!storedRefreshToken) {
    return null
  }
  
  try {
    const response = await fetch(`${getBaseUrl()}/auth/refresh`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${storedRefreshToken}`,
      },
      body: JSON.stringify({ refresh_token: storedRefreshToken }),
    })
    
    if (response.ok) {
      const data = await response.json()
      if (data.code >= 200 && data.code < 300 && data.data?.access_token) {
        const newToken = data.data.access_token
        setTokens(newToken, data.data.refresh_token)
        return newToken
      }
    }
    
    return null
  } catch (error) {
    console.error('[Auth] Token 刷新失败:', error)
    return null
  }
}

/**
 * 带自动刷新的 Token 获取
 * 如果 Token 即将过期，自动刷新
 */
export async function getValidToken(): Promise<string | null> {
  const token = getAccessToken()
  if (!token) return null
  
  if (!isTokenExpiringSoon()) {
    return token
  }
  
  // Token 即将过期，需要刷新
  if (isRefreshing) {
    // 等待正在进行的刷新完成
    return new Promise((resolve) => {
      refreshSubscribers.push((newToken) => {
        resolve(newToken)
      })
    })
  }
  
  isRefreshing = true
  
  try {
    const newToken = await refreshAuthToken()

    // 通知所有等待者
    refreshSubscribers.forEach(callback => callback(newToken))
    refreshSubscribers = []

    // 【断线风暴修复 20260930】刷新失败（被服务端拒绝/已撤销/不存在）说明会话
    // 已不可恢复，必须清空本地 token。否则调用方会 || getAccessToken() 拿到
    // 过期 access token 反复重连，每次都被 4001 拒绝形成握手风暴。
    if (!newToken) {
      clearTokens()
    }

    return newToken
  } catch (error) {
    console.error('[Auth] Token 刷新异常:', error)
    refreshSubscribers.forEach(callback => callback(null))
    refreshSubscribers = []
    clearTokens()
    return null
  } finally {
    isRefreshing = false
  }
}

/** 登出 */
export function logout(): void {
  clearTokens()
  // 触发登出事件
  window.dispatchEvent(new CustomEvent('miaoyi:logout'))
}

/** 需要登录事件 */
export function requireLogin(): void {
  clearTokens()
  window.dispatchEvent(new CustomEvent('miaoyi:require-login'))
}

/**
 * 获取带认证头的请求选项
 */
export async function getAuthHeaders(): Promise<Record<string, string>> {
  const token = await getValidToken()
  return token ? { 'Authorization': `Bearer ${token}` } : {}
}

/**
 * 注册全局请求拦截器
 */
/**
 * 创建带认证的 fetch 封装
 */
/**
 * 生成设备指纹 (简化版)
 */
/**
 * 生成请求签名 (简化版，真实签名应由后端验证)
 */
/** 认证状态检查 */
export function checkAuthStatus(): { 
  isAuthenticated: boolean
  isTokenExpired: boolean
  needsRefresh: boolean
  userData: any
} {
  const token = getAccessToken()
  if (!token) {
    return {
      isAuthenticated: false,
      isTokenExpired: true,
      needsRefresh: false,
      userData: null,
    }
  }
  
  const userData = decodeJWT(token)
  const expired = isTokenExpired()
  
  return {
    isAuthenticated: true,
    isTokenExpired: expired,
    needsRefresh: expired && !!getRefreshToken(),
    userData,
  }
}

