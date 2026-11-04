import { getAccessToken, getValidToken, refreshAuthToken, requireLogin } from './auth'
import { AppError, retry } from './errorHandler'
import { getBaseUrl } from './config'

export { getBaseUrl, setBaseUrl, resetBaseUrl } from './config'

export interface ApiResponse<T = any> {
  code: number
  message: string
  data: T
}

export interface RequestOptions extends RequestInit {
  retries?: number
  timeout?: number
  /** 认证端点（登录/注册）传 true：401 时不触发自动刷新，直接透传后端错误 */
  skipAuthRefresh?: boolean
}

function generateDeviceFingerprint(): string {
  const components = [
    navigator.userAgent,
    navigator.language,
    navigator.platform,
    `${screen.width}x${screen.height}`,
    new Date().getTimezoneOffset(),
  ]
  const raw = components.join('|')
  let hash = 0
  for (let i = 0; i < raw.length; i++) {
    const char = raw.charCodeAt(i)
    hash = ((hash << 5) - hash) + char
    hash = hash & hash
  }
  return Math.abs(hash).toString(16)
}

async function generateRequestSignature(url: string, method: string, body: string, timestamp: number, nonce: string): Promise<string> {
  const message = `${method}|${url}|${body}|${timestamp}|${nonce}`
  const encoder = new TextEncoder()
  const data = encoder.encode(message)
  const hashBuffer = await crypto.subtle.digest('SHA-256', data)
  const hashArray = Array.from(new Uint8Array(hashBuffer))
  return hashArray.map(b => b.toString(16).padStart(2, '0')).join('')
}

let isRefreshing = false
let pendingRequests: Array<(token: string | null) => void> = []

export async function request<T = any>(
  url: string,
  options: RequestOptions = {}
): Promise<ApiResponse<T>> {
  const { retries = 1, timeout = 30000, skipAuthRefresh = false, ...fetchOptions } = options

  // 使用新的 auth 模块获取有效 token（支持自动刷新）
  let token: string | null
  try {
    token = await getValidToken()
  } catch {
    token = getAccessToken()
  }

  const headers = {
    'Content-Type': 'application/json',
    ...fetchOptions.headers,
  } as Record<string, string>

  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  headers['x-device-id'] = generateDeviceFingerprint()
  
  const timestamp = Date.now()
  const nonce = Math.random().toString(36).substring(2)
  headers['x-timestamp'] = String(timestamp)
  headers['x-nonce'] = nonce

  const bodyStr = fetchOptions.body ? (typeof fetchOptions.body === 'string' ? fetchOptions.body : JSON.stringify(fetchOptions.body)) : ''
  // 【修复】crypto.subtle 仅在 secure context（HTTPS / localhost）可用；
  // 通过 http://局域网IP 访问或 Capacitor WebView 时不可用，签名生成需容错跳过。
  // 后端 REQUEST_SIGNING_ENABLED 默认关闭（不验证签名），跳过签名不影响请求。
  try {
    headers['x-signature'] = await generateRequestSignature(url, fetchOptions.method || 'GET', bodyStr, timestamp, nonce)
  } catch {
    headers['x-signature'] = ''
  }

  const makeRequest = async (retried401 = 0): Promise<ApiResponse<T>> => {
    let response: Response
    const controller = new AbortController()
    const timeoutId = setTimeout(() => controller.abort(), timeout)

    try {
      response = await fetch(`${getBaseUrl()}${url}`, {
        ...fetchOptions,
        headers,
        signal: controller.signal,
      })
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        throw new AppError('喵～思考太久了，让本喵先休息一下，稍后再试吧~', 408)
      }
      throw new AppError('喵～网络断开了，本喵正在努力重连中...', 0)
    } finally {
      clearTimeout(timeoutId)
    }

    // 401 处理：尝试刷新 token 并重试
    // 认证端点（skipAuthRefresh）除外：登录时密码错误的 401 应直接透传，
    // 不能触发刷新流程，否则用户看到的是“登录已失效”而非“用户名或密码错误”
    if (response.status === 401 && !skipAuthRefresh) {
      // 【P2修复】401 重试加上限：防止"新 token 仍被拒 → 再刷新"的串行循环
      if (retried401 >= 1) {
        requireLogin()
        throw new AppError('登录已失效，请重新登录喵～', 401)
      }

      // 避免多个请求同时触发刷新
      if (isRefreshing) {
        return new Promise((resolve, reject) => {
          pendingRequests.push((newToken) => {
            if (newToken) {
              // 使用新 token 重试
              headers['Authorization'] = `Bearer ${newToken}`
              makeRequest(retried401 + 1).then(resolve, reject)
            } else {
              reject(new AppError('登录已失效，请重新登录喵～', 401))
            }
          })
        })
      }

      isRefreshing = true
      try {
        // 使用 refreshAuthToken 刷新 token
        const newToken = await refreshAuthToken()
        if (newToken) {
          headers['Authorization'] = `Bearer ${newToken}`
          // 通知等待的请求
          pendingRequests.forEach(cb => cb(newToken))
          pendingRequests = []
          return makeRequest(retried401 + 1)
        } else {
          // 【P1修复】刷新失败也要唤醒排队的请求，否则它们的 promise 永不 settle，UI 卡死
          pendingRequests.forEach(cb => cb(null))
          pendingRequests = []
          requireLogin()
          throw new AppError('登录已失效，请重新登录喵～', 401)
        }
      } catch {
        // 【P1修复】同上：异常分支同样唤醒并清空排队请求
        pendingRequests.forEach(cb => cb(null))
        pendingRequests = []
        requireLogin()
        throw new AppError('登录已失效，请重新登录喵～', 401)
      } finally {
        isRefreshing = false
      }
    }

    if (response.status === 403) {
      // 透传后端具体原因（邀请码无效 / 账号已被禁用 等），读不到再用兜底
      let errorMessage = '喵～这里没有权限哦，让本喵看看是不是走错门了~'
      try {
        const errorData = await response.json()
        if (errorData.message) errorMessage = errorData.message
      } catch {
      }
      throw new AppError(errorMessage, 403)
    }

    if (response.status === 404) {
      throw new AppError('喵～找不到这个东西呢，它是不是躲起来了？', 404)
    }

    if (response.status === 429) {
      // 透传后端限流/账号锁定提示（如“账号已锁定 15 分钟”）
      let errorMessage = '喵～请求太频繁了，让本喵喘口气再试吧~'
      try {
        const errorData = await response.json()
        if (errorData.message) errorMessage = errorData.message
      } catch {
      }
      throw new AppError(errorMessage, 429)
    }

    if (!response.ok) {
      let errorMessage = '喵～本喵有点懵，没理解你的意思呢~'
      try {
        const errorData = await response.json()
        if (errorData.message) {
          errorMessage = errorData.message
        }
      } catch {
      }
      throw new AppError(errorMessage, response.status)
    }

    let result: ApiResponse<T>
    try {
      result = await response.json()
    } catch {
      throw new AppError('喵～服务器好像说方言了，本喵没听懂~', 500)
    }

    if (result.code < 200 || result.code >= 300) {
      throw new AppError(result.message || '喵～本喵好像搞砸了，请见谅~', result.code)
    }

    return result
  }

  return retries > 1 ? retry(makeRequest, retries) : makeRequest()
}

export async function login(username: string, password: string) {
  return request('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
    skipAuthRefresh: true,
  })
}

export async function register(username: string, phone: string, password: string, inviteCode: string = '') {
  return request('/auth/register', {
    method: 'POST',
    body: JSON.stringify({ username, phone, password, invite_code: inviteCode }),
    skipAuthRefresh: true,
  })
}

export async function getAuthConfig() {
  return request('/auth/config', { skipAuthRefresh: true })
}

export async function getProfile() {
  return request('/user/profile')
}

export async function submitFeedback(content: string) {
  return request('/feedback', {
    method: 'POST',
    body: JSON.stringify({ content }),
  })
}

export async function updateNickname(nickname: string) {
  return request('/user/nickname', {
    method: 'PUT',
    body: JSON.stringify({ nickname }),
  })
}

export async function updatePassword(oldPassword: string, newPassword: string) {
  return request('/user/password', {
    method: 'PUT',
    body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
  })
}

export async function logoutApi() {
  // 容忍失败：即便后端返回 401（token 已失效），前端仍要清本地
  try {
    return await request('/auth/logout', { method: 'POST' })
  } catch (e) {
    // 忽略错误，确保本地一定清空
    return { code: 0, message: 'logout best-effort', data: null as any }
  }
}

export async function clearUserProfile() {
  return request('/user/profile', { method: 'DELETE' })
}

export async function checkin() {
  return request('/user/checkin', {
    method: 'POST',
  })
}

export async function getGrowthData() {
  return request('/growth/data')
}

// ========== App 版本更新 ==========
export interface AppVersionInfo {
  platform: string
  latest_version_code: number
  latest_version_name: string
  current_version_code: number
  has_update: boolean
  force_update: boolean
  min_supported_version_code: number
  update_title: string
  release_notes: string[]
  download_url: string
  file_size: number
  published_at: string
}

export async function checkAppUpdate(
  platform: string,
  versionCode: number,
): Promise<AppVersionInfo | null> {
  try {
    const result = await request<AppVersionInfo>(
      `/version/check?platform=${encodeURIComponent(platform)}&version_code=${versionCode}`,
    )
    if (result.code === 200 && result.data) return result.data
    return null
  } catch {
    // 更新检查失败保持静默，不打扰正常使用
    return null
  }
}

export async function getMemories(limit?: number) {
  // limit 可选：记忆页分类计数需要大窗口（后端上限 500），避免统计被默认 50 条截断
  const result = await request(`/memory/list${limit ? `?limit=${limit}` : ''}`)
  const mappedData = (result.data || []).map((item: any) => ({
    // 【bug修复 2026-08】旧版 id: item.key || item.id —— DB 里同 key 存在重复行时，
    // React 列表爆大量 duplicate key 警告，且删除接口拿到的是 key 而非行 id。
    // 改用数字行 id（后端 DELETE /api/memory/{id} 已兼容）。
    id: item.id != null ? String(item.id) : String(item.key || ''),
    content: item.value || item.content,
    timestamp: item.created_ts || item.timestamp,
    pinned: Boolean(item.pinned),
  }))
  return { ...result, data: mappedData }
}

export async function deleteMemory(id: string) {
  return request(`/memory/${id}`, {
    method: 'DELETE',
  })
}

/** 【L1 记忆卡片流】编辑记忆内容 */
export async function editMemory(id: string, content: string) {
  return request(`/memory/${id}`, {
    method: 'PATCH',
    body: JSON.stringify({ content }),
  })
}

/** 【L1 记忆卡片流】钉住/取消钉住记忆 */
export async function toggleMemoryPin(id: string, pinned: boolean) {
  return request(`/memory/${id}/pin`, {
    method: 'POST',
    body: JSON.stringify({ pinned }),
  })
}

export async function clearMemories(days?: number) {
  return request('/memory/clear', {
    method: 'POST',
    body: days ? JSON.stringify({ days }) : undefined,
  })
}

export async function synthesizeVoice(text: string) {
  return request('/voice/synthesize', {
    method: 'POST',
    body: JSON.stringify({ text }),
    timeout: 15000,
  })
}

/** 【首句优先】拉取分段 TTS 的第 index 段（后端最长等待 ~15s 合成完成） */
export async function fetchVoiceSegment(taskId: string, index: number) {
  return request(`/voice/segment?task_id=${encodeURIComponent(taskId)}&index=${index}`, {
    timeout: 20000,
  })
}

export async function getSessionList() {
  const result = await request('/session/list')
  const rawSessions = result.data?.sessions || result.data || []
  const mappedData = rawSessions.map((item: any) => ({
    id: item.session_id || item.id,
    name: item.name || '新会话',
    lastMessage: item.last_message || '',
    lastTime: item.last_active_ts || item.lastTime || 0,
    unreadCount: item.unread_count || item.unreadCount || 0,
    avatar: item.avatar || '',
  }))
  return { ...result, data: mappedData }
}

/** 【视觉能力】聊天图片上传：返回 { url: '/uploads-chat/img_xxx.png', kind: 'image' } */
export async function uploadChatImage(file: File) {
  const formData = new FormData()
  formData.append('file', file)

  let token: string | null
  try {
    token = await getValidToken()
  } catch {
    token = getAccessToken()
  }
  const headers: Record<string, string> = {}
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  const response = await fetch(`${getBaseUrl()}/upload/file`, {
    method: 'POST',
    body: formData,
    headers,
  })
  return response.json()
}

export async function createSession(name: string) {
  const result = await request('/session/create', {
    method: 'POST',
    body: JSON.stringify({ name }),
  })
  const mappedData = {
    id: result.data?.session_id || result.data?.id || Date.now().toString(),
    name: name || '新会话',
    lastMessage: '',
    lastTime: Date.now(),
    unreadCount: 0,
  }
  return { ...result, data: mappedData }
}

export async function deleteSession(id: string) {
  return request(`/session/${id}`, {
    method: 'DELETE',
  })
}

export async function deleteMessage(messageId: number) {
  return request(`/message/${messageId}`, {
    method: 'DELETE',
  })
}

export async function regenerateMessage(text: string) {
  return request('/message/regenerate', {
    method: 'POST',
    body: JSON.stringify({ text }),
    retries: 2,
    timeout: 60000,
  })
}

export async function getConversations() {
  return request('/conversations')
}

export async function clearAllConversations() {
  return request('/conversation/clear', {
    method: 'POST',
  })
}

export async function searchMemories(query: string) {
  return request(`/memory/search?query=${encodeURIComponent(query)}`)
}

export async function getMemoryTags() {
  return request('/memory/tags')
}

export async function getMemoriesByTag(tag: string) {
  return request(`/memory/tag/${encodeURIComponent(tag)}`)
}

export async function addMemoryTag(memoryId: number, tag: string) {
  return request('/memory/tag', {
    method: 'POST',
    body: JSON.stringify({ memory_id: memoryId, tag }),
  })
}

export async function removeMemoryTag(memoryId: number, tag: string) {
  return request('/memory/tag', {
    method: 'DELETE',
    body: JSON.stringify({ memory_id: memoryId, tag }),
  })
}

export async function getEmotionalState() {
  return request('/emotion/state')
}

export async function getEmotionalHistory() {
  return request('/emotion/history')
}

export async function getRelationshipStatus() {
  return request('/relationship/status')
}

export async function getIntimacyProgress() {
  return request('/relationship/intimacy')
}

export async function getRelationshipAnniversary() {
  return request('/relationship/anniversary')
}

/** 【L6 猫咪日记】今日日记（后端幂等：已有则返回，素材足够时懒生成） */
export async function getDiaryToday() {
  return request('/diary/today')
}

/** 【L6 回忆相册】历史日记列表 */
export async function getDiaryList(limit = 90) {
  return request(`/diary/list?limit=${limit}`)
}

/** 【L6 那年今日 + 纪念卡片】历史同月同日日记 + 关系里程碑 + 签到状态 */
export async function getDiaryMemoryLane() {
  return request('/diary/memory-lane')
}

/** 【L7 朋友圈】喵艺的生活动态列表 */
export async function getMoments(limit = 20) {
  return request(`/moments/list?limit=${limit}`)
}

/** 【L7 朋友圈】点赞/取消点赞动态 */
export async function toggleMomentLike(id: number, liked: boolean) {
  return request(`/moments/${id}/like`, {
    method: 'POST',
    body: JSON.stringify({ liked }),
  })
}

export async function uploadSticker(file: File) {
  const formData = new FormData()
  formData.append('file', file)

  // 使用新的 auth 模块获取有效 token
  let token: string | null
  try {
    token = await getValidToken()
  } catch {
    token = getAccessToken()
  }

  const headers: Record<string, string> = {}
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  const response = await fetch(`${getBaseUrl()}/sticker/upload`, {
    method: 'POST',
    body: formData,
    headers,
  })

  if (!response.ok) {
    // 401 时尝试刷新
    if (response.status === 401) {
      try {
        const newToken = await refreshAuthToken()
        if (newToken) {
          headers['Authorization'] = `Bearer ${newToken}`
          const retryResp = await fetch(`${getBaseUrl()}/sticker/upload`, {
            method: 'POST',
            body: formData,
            headers,
          })
          if (!retryResp.ok) {
            throw new AppError('上传失败', retryResp.status)
          }
          return retryResp.json()
        }
      } catch {
        requireLogin()
      }
    }

    let errorMessage = '上传失败'
    try {
      const errorData = await response.json()
      if (errorData.message) {
        errorMessage = errorData.message
      }
    } catch {
    }
    throw new AppError(errorMessage, response.status)
  }

  return response.json()
}

export interface OnboardingOption {
  id: string
  title: string
  description: string
  trigger_text: string
}

export interface OnboardingResponse {
  is_first_time: boolean
  greeting: string
  options: OnboardingOption[]
}

export async function getOnboarding(): Promise<ApiResponse<OnboardingResponse>> {
  return request('/onboarding')
}

export interface DailyFragmentResponse {
  fragment: string
  date: string
}

export async function getDailyFragment(): Promise<ApiResponse<DailyFragmentResponse>> {
  return request('/daily_fragment')
}

export interface PersonalityPreset {
  name: string
  emoji: string
  description: string
  persona: string
  tone: string
  speech_style: string
}

export interface PersonalityResponse {
  current: string
  presets: Record<string, PersonalityPreset>
}

export async function getPersonality(): Promise<ApiResponse<PersonalityResponse>> {
  return request('/personality')
}

export async function setPersonality(personality: string): Promise<ApiResponse<{ success: boolean; message: string; current: string }>> {
  return request('/personality', {
    method: 'POST',
    body: JSON.stringify({ personality }),
  })
}

export async function getVoiceMode(): Promise<ApiResponse<{ voice_mode: number; voice_mode_name: string; available_modes: any[] }>> {
  return request('/voice-mode')
}

export async function setVoiceMode(mode: number): Promise<ApiResponse<{ voice_mode: number; voice_mode_name: string }>> {
  return request('/voice-mode', {
    method: 'POST',
    body: JSON.stringify({ mode }),
  })
}

export async function exportChat(format: 'txt' | 'markdown' = 'txt'): Promise<ApiResponse<{ url: string }>> {
  return request('/export/chat', {
    method: 'POST',
    body: JSON.stringify({ format }),
  })
}

// 【L8 角色卡导出】SillyTavern V2 兼容 .json（含世界书=喵艺的记忆）
export async function exportCharacterCard(): Promise<ApiResponse<{ url: string }>> {
  return request('/character-card/export', { method: 'GET' })
}

// 【L8 插件机制】
export interface PluginInfo {
  id: string
  name: string
  version: string
  description: string
  author: string
  enabled: boolean
  fragment_count: number
}

export async function getPlugins(): Promise<ApiResponse<{ plugins: PluginInfo[] }>> {
  return request('/plugins/list', { method: 'GET' })
}

export async function togglePlugin(id: string, enabled: boolean): Promise<ApiResponse<{ id: string; enabled: boolean }>> {
  return request(`/plugins/${encodeURIComponent(id)}/toggle`, {
    method: 'POST',
    body: JSON.stringify({ enabled }),
  })
}
