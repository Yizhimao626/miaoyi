import { useEffect, useRef, useCallback, useState } from 'react'
import { getAccessToken, getValidToken, clearTokens } from '../utils/auth'
import type { Message } from '../types'
import { useChatStore } from '../store/chatStore'
import { useAudioManager } from './useAudioManager'
import { useNetwork } from './useNetwork'
// 【通话功能修复】useWebSocket 统一处理 call_event/incoming_call/call_subtitle/chat_record
// 事件 + 通话二进制音频。原 useEnhanceEvents 自建第二条主 WS（URL 还拼成 /api/ws 连不上），
// 导致连接互相覆盖、通话事件丢失。现已合并到本 hook 单条 WS，删除 useEnhanceEvents。
import { useCallStore } from '../store/callStore'

// ============ 通话二进制音频播放（AudioContext 队列，避免重叠） ============
let __callAudioCtx: AudioContext | null = null
const __callAudioQueue: ArrayBuffer[] = []
let __callAudioPlaying = false
// 【barge-in】当前正在播放的音频块节点引用（打断时需 stop）
let __callAudioNodeRef: AudioBufferSourceNode | null = null
// 【barge-in】打断后的残余音频丢弃窗：后端收到 barge_in 前可能还在推旧回合音频，
// 这些迟到块直接丢弃，防止被打断的话音"还魂"
let __bargeInUntil = 0

// 【回声消除】喵艺 TTS 播报期间 window.__botSpeaking = true，useVoiceDialog 据此丢弃录音片段，
// 防止喵艺的声音被麦克风识别成"用户输入"形成无限回声循环。
// 通过 window 暴露（与 __sendCallWs 同风格），useVoiceDialog 读取。
// __botSpeakingSince 记录开播时刻，供 VAD 打断的"开口静默窗"判断使用。
function __setBotSpeaking(v: boolean): void {
  const w = window as unknown as { __botSpeaking?: boolean; __botSpeakingSince?: number }
  w.__botSpeaking = v
  if (v) w.__botSpeakingSince = Date.now()
}

function __ensureCallAudioCtx(): AudioContext | null {
  if (typeof window === 'undefined') return null
  if (!__callAudioCtx) {
    try {
      __callAudioCtx = new (window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext)()
    } catch {
      return null
    }
  }
  return __callAudioCtx
}

/**
 * 【通话无声修复】必须在用户点击“打电话”的手势里解锁 AudioContext。
 * 移动端 WebView/浏览器默认 suspended，等首包音频到达再 resume 已脱离用户手势，
 * 会被自动播放策略拦截，表现为“接通了但没声音”。
 */
function __unlockCallAudio(): void {
  const ctx = __ensureCallAudioCtx()
  if (!ctx) return
  try {
    if (ctx.state === 'suspended') void ctx.resume()
    // 播放一个 1 帧静音 buffer，完成 iOS/Android 的音频解锁
    const buf = ctx.createBuffer(1, 1, 22050)
    const src = ctx.createBufferSource()
    src.buffer = buf
    src.connect(ctx.destination)
    src.start(0)
  } catch {
    /* ignore */
  }
}
if (typeof window !== 'undefined') {
  ;(window as unknown as { __unlockCallAudio?: () => void }).__unlockCallAudio = __unlockCallAudio
}

async function __playCallAudioChunk(buf: ArrayBuffer): Promise<void> {
  const ctx = __ensureCallAudioCtx()
  if (!ctx) return
  if (ctx.state === 'suspended') {
    try { await ctx.resume() } catch { /* ignore */ }
  }
  try {
    const audioBuf = await ctx.decodeAudioData(buf.slice(0))
    // 【barge-in】decodeAudioData 是异步的：解码期间用户打断的话，该块已作废，
    // 直接丢弃，防止被打断的话音在解码窗口落地后仍外放
    if (Date.now() < __bargeInUntil) return
    return new Promise((resolve) => {
      const node = ctx.createBufferSource()
      __callAudioNodeRef = node
      node.buffer = audioBuf
      node.connect(ctx.destination)
      node.onended = () => {
        if (__callAudioNodeRef === node) __callAudioNodeRef = null
        resolve()
      }
      node.start()
    })
  } catch (e) {
    console.warn('[WebSocket] 通话音频解码失败', e)
  }
}

// 【修复】drain 代次令牌：每轮 drain 开始时递增。防止上一轮 drain 的 400ms 尾音
// 冷却定时器错误解除新一轮播报的回声抑制（旧版竞态：A 轮结束后 B 轮开播，
// A 的定时器到点把 B 正在使用的 __botSpeaking 置 false → 回声抑制中途失效）
let __drainGen = 0

async function __drainCallAudioQueue(): Promise<void> {
  if (__callAudioPlaying) return
  __callAudioPlaying = true
  const myGen = ++__drainGen
  // 喵艺开始播报，标记回声抑制
  __setBotSpeaking(true)
  try {
    while (__callAudioQueue.length > 0) {
      const buf = __callAudioQueue.shift()!
      try {
        await __playCallAudioChunk(buf)
      } catch (e) {
        // 【修复】单块播放失败不中断队列（如 AudioContext 关闭），继续播下一块
        console.warn('[WebSocket] 通话音频块播放失败', e)
      }
    }
  } finally {
    // 【bug修复】无论播报是否异常，都必须复位播放中标志，并在尾音冷却后解除回声抑制。
    // 旧版若 __playCallAudioChunk 抛异常（如 AudioContext 关闭时 node.start() 抛），
    // while 中断后 __callAudioPlaying 与 __botSpeaking 永远卡 true →
    // 通话中 ASR 永远忽略用户声音（"说话没反应"）。
    __callAudioPlaying = false
    // 播报结束，留 400ms 尾音冷却期再解除抑制，避免 ASR 捕到尾音。
    // 【修复】仅当此后没有新 drain 轮次启动时才解除（代次检查）
    setTimeout(() => {
      if (myGen === __drainGen) __setBotSpeaking(false)
    }, 400)
  }
}

function __enqueueCallAudio(buf: ArrayBuffer): void {
  // 【barge-in】打断后的迟到残余音频（已作废回合的推送）直接丢弃
  if (Date.now() < __bargeInUntil) return
  __callAudioQueue.push(buf)
  void __drainCallAudioQueue()
}

/**
 * 【barge-in 语音打断】用户在喵艺播报期间开口说话（VAD 检测）时调用：
 * 清空待播队列 + 立即停止当前播放块 + 解除回声抑制，并进入 1.5s 残余丢弃窗。
 * useVoiceDialog 的 VAD onSpeechStart 触发，随后另发 {type:'barge_in'} 通知后端作废旧回合。
 */
function __bargeInCall(): void {
  __callAudioQueue.length = 0
  if (__callAudioNodeRef) {
    try { __callAudioNodeRef.stop() } catch { /* ignore */ }
    __callAudioNodeRef = null
  }
  // 不动 __callAudioPlaying：旧 drain 循环会随 node.onended/空队列自行退出，避免双循环竞争
  __bargeInUntil = Date.now() + 1500
  __setBotSpeaking(false)
}
;(window as unknown as { __bargeInCall?: () => void }).__bargeInCall = __bargeInCall

interface UseWebSocketReturn {
  sendMessage: (text: string, replyTo?: string) => void
  reconnect: () => void
  retryMessage: (messageId: string, text: string, replyTo?: string) => void
  connectionStatus: 'connecting' | 'connected' | 'disconnected' | 'auth_failed'
  isConnected: boolean
}

// 自动重试上限：首次失败自动重试 1 次，第二次失败才显示红色感叹号
const MAX_AUTO_RETRY = 1
// 自动重试延迟（毫秒）- 给网络一个恢复窗口
const AUTO_RETRY_DELAY = 1500

// WebSocket URL 配置
// 统一由 utils/config.ts 解析（localStorage 自定义地址 → VITE_WS_URL → /ws）
// 【历史教训】旧版开发环境直连后端 ws://hostname:8001/ws，手机端无法直接访问电脑 8001 端口
// （防火墙阻止），导致手机 WebSocket 连接失败、登录后无法使用。改用统一地址解析后，
// API 与 WebSocket 始终指向同一后端（http→ws、https→wss 派生）。
import { getWsUrl as resolveWsUrl } from '../utils/config'
async function getWsUrl(): Promise<string> {
  return resolveWsUrl()
}
const HEARTBEAT_INTERVAL_INITIAL = 30000
const HEARTBEAT_INTERVAL_STABLE = 60000
const HEARTBEAT_TIMEOUT = 120000
const HEARTBEAT_STABLE_THRESHOLD = 120000
const RECONNECT_MAX_DELAY = 30000
const MAX_RECONNECT_ATTEMPTS = 10
const MAX_HEARTBEAT_FAILURES = 3
const MAX_MESSAGE_HISTORY = 100
const QUEUE_PERSIST_KEY = 'miaoyi_ws_message_queue'

interface QueuedMessage {
  text: string
  replyTo?: string
  imageUrl?: string  // 【视觉能力】随消息发送的图片 URL（/uploads-chat/...）
  priority: number  // 0=normal, 1=high (user messages)
  retries: number
  maxRetries: number
  addedAt: number
  clientMessageId?: string  // 客户端消息 ID，用于消息关联
  sessionId?: string  // 【跨会话修复】消息所属会话，重连 flush / 回复路由使用
}

// 【跨会话修复】待处理消息的归属信息：用户消息 id + 发送时所在会话
interface PendingEntry {
  userMsgId: string
  sessionId: string
}

export function useWebSocket(): UseWebSocketReturn {
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimeoutRef = useRef<number | null>(null)
  const heartbeatIntervalRef = useRef<number | null>(null)
  const reconnectAttemptRef = useRef(0)
  const lastMessageTimeRef = useRef(Date.now())
  const messageQueueRef = useRef<QueuedMessage[]>([])
  const isConnectingRef = useRef(false)
  const isConnectedRef = useRef(false)
  const connectionStartTimeRef = useRef(Date.now())
  const heartbeatFailureCountRef = useRef(0)
  const messageIdCacheRef = useRef<Map<string, number>>(new Map())
  const duplicateCountRef = useRef(0)
  const totalMessageCountRef = useRef(0)
  // 存储待处理的消息 ID 映射（client_message_id → 消息归属{用户消息id, 会话id}）
  // 【跨会话修复】回复到达时据此写回原会话，即使用户已切换会话也不会串台
  const pendingMessageIdRef = useRef<Map<string, PendingEntry>>(new Map())
  // 自动重试计数器（clientMessageId → 已重试次数）
  // 首次失败自动重试 1 次，第二次失败才标记为 failed 显示红色感叹号
  const messageRetryCountRef = useRef<Map<string, number>>(new Map())

  const [connectionStatus, setConnectionStatus] = useState<
    'connecting' | 'connected' | 'disconnected' | 'auth_failed'
  >('disconnected')

  const { playAudio, playAudioSequence, stopAudio } = useAudioManager()
  const { isOnline } = useNetwork()

  // 智能超时计算 - 根据网络状态动态调整
  const getSmartTimeout = useCallback(() => {
    // 基础超时时间
    const baseTimeout = 30000 // 30秒
    
    // 检测网络类型（如果可用）
    const connection = (navigator as any).connection || (navigator as any).mozConnection
    if (connection) {
      const effectiveType = connection.effectiveType
      const downlink = connection.downlink
      
      // 根据网络类型调整超时
      switch (effectiveType) {
        case 'slow-2g':
          return baseTimeout * 3  // 90秒 - 慢速网络
        case '2g':
          return baseTimeout * 2  // 60秒
        case '3g':
          return baseTimeout * 1.5  // 45秒
        case '4g':
        case 'wifi':
          return baseTimeout  // 30秒 - 正常网络
      }
      
      // 根据下行速度调整
      if (downlink && downlink < 1) {
        return baseTimeout * 2  // 慢速连接
      }
    }
    
    // 如果离线，不设置超时
    if (!isOnline) {
      return 0  // 不设置超时
    }
    
    return baseTimeout
  }, [isOnline])

  const {
    setMessages,
    addMessage,
    appendStreamText,
    setConnectionStatus: setStoreConnectionStatus,
    setEmotionalState,
    setGrowthData,
    setIsTyping,
    setThinkingStatus,
    addPendingTimeout,
    clearPendingTimeout,
    markMessageAsFailed,
    markMessageAsRetrying,
    currentSessionId,
  } = useChatStore()

  const storeActionsRef = useRef({
    setMessages,
    addMessage,
    appendStreamText,
    setStoreConnectionStatus,
    setEmotionalState,
    setGrowthData,
    setIsTyping,
    setThinkingStatus,
    addPendingTimeout,
    clearPendingTimeout,
    markMessageAsFailed,
    markMessageAsRetrying,
    currentSessionId,
  })

  useEffect(() => {
    storeActionsRef.current = {
      setMessages,
      addMessage,
      appendStreamText,
      setStoreConnectionStatus,
      setEmotionalState,
      setGrowthData,
      setIsTyping,
      setThinkingStatus,
      addPendingTimeout,
      clearPendingTimeout,
      markMessageAsFailed,
      markMessageAsRetrying,
      currentSessionId,
    }
  }, [setMessages, addMessage, appendStreamText, setStoreConnectionStatus, setEmotionalState, setGrowthData, setIsTyping, setThinkingStatus, addPendingTimeout, clearPendingTimeout, markMessageAsFailed, markMessageAsRetrying, currentSessionId])

  const updateConnectionStatus = useCallback(
    (status: 'connecting' | 'connected' | 'disconnected' | 'auth_failed') => {
      setConnectionStatus(status)
      storeActionsRef.current.setStoreConnectionStatus(status)
    },
    []
  )

  // ============ 消息队列持久化 ============

  const loadQueueFromStorage = useCallback(() => {
    try {
      const stored = localStorage.getItem(QUEUE_PERSIST_KEY)
      if (stored) {
        const parsed = JSON.parse(stored)
        if (Array.isArray(parsed)) {
          // 恢复未发送的消息 (最多保留10条)
          // 【跨会话修复】保留 clientMessageId/sessionId/maxRetries：旧版只存
          // text/replyTo，恢复后生成新 client id，破坏服务端幂等且回复无法配对
          messageQueueRef.current = parsed.slice(-10).map((m: any) => ({
            ...m,
            retries: 0,
            addedAt: Date.now(),
          }))
          if (messageQueueRef.current.length > 0) {
            console.log(`[WebSocket] 恢复 ${messageQueueRef.current.length} 条未发送消息`)
          }
        }
      }
    } catch (e) {
      console.warn('[WebSocket] 恢复消息队列失败:', e)
    }
  }, [])

  const persistQueue = useCallback(() => {
    try {
      const toPersist = messageQueueRef.current.map(
        ({ text, replyTo, priority, addedAt, clientMessageId, sessionId, maxRetries }) => ({
          text,
          replyTo,
          priority,
          addedAt,
          clientMessageId,
          sessionId,
          maxRetries,
        })
      )
      if (toPersist.length > 0) {
        localStorage.setItem(QUEUE_PERSIST_KEY, JSON.stringify(toPersist))
      } else {
        localStorage.removeItem(QUEUE_PERSIST_KEY)
      }
    } catch (e) {
      // localStorage 可能被禁用，忽略错误
    }
  }, [])

  // ============ 心跳机制 ============

  const sendHeartbeat = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'ping', ts: Date.now() }))
    }
  }, [])

  const getCurrentHeartbeatInterval = useCallback(() => {
    const connectionDuration = Date.now() - connectionStartTimeRef.current
    return connectionDuration >= HEARTBEAT_STABLE_THRESHOLD
      ? HEARTBEAT_INTERVAL_STABLE
      : HEARTBEAT_INTERVAL_INITIAL
  }, [])

  const startHeartbeat = useCallback(() => {
    if (heartbeatIntervalRef.current) {
      clearTimeout(heartbeatIntervalRef.current)
    }

    const runHeartbeat = () => {
      const now = Date.now()

      if (now - lastMessageTimeRef.current > HEARTBEAT_TIMEOUT) {
        heartbeatFailureCountRef.current++
        console.warn(
          `[WebSocket] Heartbeat timeout (${heartbeatFailureCountRef.current}/${MAX_HEARTBEAT_FAILURES})`
        )

        if (heartbeatFailureCountRef.current >= MAX_HEARTBEAT_FAILURES) {
          console.warn('[WebSocket] Max heartbeat failures reached, reconnecting...')
          if (wsRef.current) {
            wsRef.current.close(1006, 'Heartbeat timeout')
          }
        }
      } else {
        heartbeatFailureCountRef.current = 0
        sendHeartbeat()
      }
    }

    runHeartbeat()

    const scheduleNextHeartbeat = () => {
      heartbeatIntervalRef.current = window.setTimeout(() => {
        runHeartbeat()
        scheduleNextHeartbeat()
      }, getCurrentHeartbeatInterval())
    }

    scheduleNextHeartbeat()
  }, [sendHeartbeat, getCurrentHeartbeatInterval])

  const stopHeartbeat = useCallback(() => {
    if (heartbeatIntervalRef.current) {
      clearTimeout(heartbeatIntervalRef.current)
      heartbeatIntervalRef.current = null
    }
  }, [])

  // ============ 消息去重 ============

  const checkDuplicateMessage = useCallback((msgId: string): boolean => {
    totalMessageCountRef.current++

    if (messageIdCacheRef.current.has(msgId)) {
      duplicateCountRef.current++
      const hitRate = ((duplicateCountRef.current / totalMessageCountRef.current) * 100).toFixed(2)
      console.warn(`[WebSocket] Duplicate message detected: ${msgId} (hit rate: ${hitRate}%)`)
      return true
    }

    messageIdCacheRef.current.set(msgId, Date.now())

    if (messageIdCacheRef.current.size > MAX_MESSAGE_HISTORY) {
      const oldestKey = messageIdCacheRef.current.keys().next().value
      if (oldestKey) {
        messageIdCacheRef.current.delete(oldestKey)
      }
    }

    return false
  }, [])

  // ============ 消息队列冲刷 ============

  const flushMessageQueue = useCallback(() => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return

    // 按优先级排序 (高优先级先发送)，相同优先级按添加时间排序
    const sorted = [...messageQueueRef.current].sort((a, b) => {
      if (b.priority !== a.priority) return b.priority - a.priority
      return a.addedAt - b.addedAt  // 时间越越早越优先
    })
    messageQueueRef.current = sorted

    const remaining: QueuedMessage[] = []
    // 被丢弃(过期/超次数)的消息 id：需同步把 store 消息标 failed，避免永久转圈
    const droppedIds: string[] = []
    // 成功发送的消息：离线期间无法 arm 超时，发送成功后补 arm
    const armed: Array<{ id: string; text: string; replyTo?: string }> = []
    const now = Date.now()

    for (const msg of messageQueueRef.current) {
      if (wsRef.current.readyState !== WebSocket.OPEN) {
        remaining.push(msg)
        continue
      }

      // 跳过过期的消息（超过5分钟未发送）
      if (now - msg.addedAt > 5 * 60 * 1000) {
        console.warn(`[WebSocket] 跳过过期消息: ${msg.text}`)
        if (msg.clientMessageId) droppedIds.push(msg.clientMessageId)
        continue
      }

      if (msg.retries >= msg.maxRetries) {
        console.warn(`[WebSocket] 消息超过最大重试次数，丢弃: ${msg.text}`)
        if (msg.clientMessageId) droppedIds.push(msg.clientMessageId)
        continue
      }

      try {
        // 如果没有 clientMessageId，生成一个新的
        const msgClientId = msg.clientMessageId || `msg_${now}_${Math.random().toString(36).substr(2, 9)}`
        
        wsRef.current.send(
          JSON.stringify({
            type: 'message',
            text: msg.text,
            context: '',
            reply_to: msg.replyTo,
            client_message_id: msgClientId,  // 包含 client_message_id
            ...(msg.imageUrl ? { image_url: msg.imageUrl } : {}),
          })
        )
        // 成功发送，更新 clientMessageId
        msg.clientMessageId = msgClientId
        msg.retries = 0  // 重置重试计数
        armed.push({ id: msgClientId, text: msg.text, replyTo: msg.replyTo })
        // 成功发送，不加入 remaining
      } catch (e) {
        msg.retries++
        remaining.push(msg)
      }
    }

    messageQueueRef.current = remaining
    persistQueue()

    // 丢弃的队列消息同步标记 UI 失败（旧版仅丢弃队列条目，store 消息永久卡 sending）
    droppedIds.forEach((id) => storeActionsRef.current.markMessageAsFailed(id))

    // 补 arm 30s 超时；addPendingTimeout 会自动清掉同 id 旧定时器，重复 arm 安全
    armed.forEach(({ id, text, replyTo }) => {
      const timeoutId = window.setTimeout(() => {
        const state = useChatStore.getState()
        // 跨全部会话定位该消息（用户可能已切换会话）
        let sessionId = state.currentSessionId
        let msg: Message | undefined
        state.messagesBySession.forEach((msgs, sid) => {
          const found = msgs.find((m) => m.id === id)
          if (found) {
            msg = found
            sessionId = sid
          }
        })
        if (msg && msg.status === 'sending') {
          storeActionsRef.current.clearPendingTimeout(id)
          handleSendFailureRef.current({ userMsgId: id, sessionId }, text, replyTo)
        }
      }, 30000)
      storeActionsRef.current.addPendingTimeout(id, timeoutId)
    })

    if (remaining.length > 0) {
      console.warn(`[WebSocket] ${remaining.length} 条消息待重试`)
      // 安排稍后重试，使用指数退避
      const retryDelay = Math.min(1000 * Math.pow(1.5, Math.max(...remaining.map(r => r.retries))), 10000)
      setTimeout(flushMessageQueue, retryDelay)
    }
  }, [persistQueue])

  // ============ WebSocket 创建 ============

  const createWebSocket = useCallback(async () => {
    if (isConnectingRef.current) return
    // 【风暴修复 20260930】未登录静默早退：旧版先 setState('connecting') 再在 token 检查处
    // setState('disconnected')，而 App 的重连 effect 监听 disconnected 会再次 reconnect()——
    // 未登录时形成 connecting/disconnected 状态交替风暴（CPU 满载、页面无响应）。
    // 必须在触碰任何状态前退出；未登录的 disconnected 状态由挂载 effect 一次性设置。
    {
      const earlyToken = getAccessToken()
      if (!earlyToken || typeof earlyToken !== 'string' || earlyToken.trim().length === 0) {
        return
      }
    }
    if (isConnectedRef.current && wsRef.current?.readyState === WebSocket.OPEN) {
      // 【经验修正 812844】已打开但未触发 onopen 时，手动补齐 UI 状态
      console.log('[WebSocket] 连接已 OPEN，补齐 UI 连接状态')
      updateConnectionStatus('connected')
      // 立即冲刷一次队列
      flushMessageQueue()
      return
    }

    // 【双连接修复】摘除并关闭旧连接：先把 wsRef 置空，旧连接随后的
    // onclose/onerror/onmessage 会被各处理器的 stale 守卫（wsRef.current !== ws）忽略，
    // 不会再出现"旧连接自动重连与新连接互相覆盖 / 孤儿连接"问题。
    const oldWs = wsRef.current
    if (oldWs) {
      wsRef.current = null
      isConnectedRef.current = false
      try { oldWs.close() } catch { /* ignore */ }
    }
    // 取消旧 onclose 挂起的自动重连定时器，防止事后触发产生竞争
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current)
      reconnectTimeoutRef.current = null
    }

    isConnectingRef.current = true

    stopHeartbeat()
    updateConnectionStatus('connecting')

    // 【轮换兼容】优先 getValidToken：临近过期自动刷新，避免 App 恢复/网络切换后
    // access token 已过期导致重连被 4001 拒绝（旧行为直接拿本地 token，过期必失败）
    // 【断线风暴修复 20260930】去掉 || getAccessToken() 兜底：getValidToken 返回 null
    // 意味着刷新已失败、token 已被清除，此时拿过期 token 硬连只会被 4001 拒绝，
    // 与 onerror/onclose 状态交替形成紧循环握手风暴（每秒数十次）
    const token = await getValidToken()
    // 严格 token 检查：null / undefined / 空字符串都视为未登录
    if (!token || typeof token !== 'string' || token.trim().length === 0) {
      console.warn('[WebSocket] 未获取到有效 token，取消连接（请先登录）')
      isConnectingRef.current = false
      updateConnectionStatus('disconnected')
      return
    }

    // 动态获取 WebSocket URL（支持运行时端口检测）
    const wsBaseUrl = await getWsUrl()
    const params = new URLSearchParams()
    params.set('token', token.trim())
    const wsUrl = `${wsBaseUrl}?${params.toString()}`
    // 安全日志：不打印完整 URL（含 token），避免 token 泄露到浏览器控制台/日志
    console.log(`[WebSocket] 连接到: ${wsBaseUrl} (token 已隐藏, 长度=${token.trim().length})`)
    // 【健壮性修复】非法自定义服务器地址时 WebSocket 构造器会同步抛错，
    // 旧版无 try/catch → isConnecting 永久 true，之后所有重连都被守卫挡回
    let ws: WebSocket
    try {
      ws = new WebSocket(wsUrl)
    } catch (e) {
      console.warn('[WebSocket] 无法创建连接（服务器地址可能非法）:', wsBaseUrl, e)
      isConnectingRef.current = false
      updateConnectionStatus('disconnected')
      return
    }

    ws.onopen = () => {
      isConnectingRef.current = false
      isConnectedRef.current = true
      console.log('[WebSocket] Connected')
      updateConnectionStatus('connected')
      reconnectAttemptRef.current = 0
      heartbeatFailureCountRef.current = 0
      connectionStartTimeRef.current = Date.now()
      lastMessageTimeRef.current = Date.now()
      startHeartbeat()
      flushMessageQueue()
    }

    ws.onclose = (event) => {
      // stale 守卫：该连接已被 createWebSocket 摘除替换（手动/网络重连场景），
      // 旧连接的关闭不得改动共享状态、不得再挂起重连
      if (wsRef.current !== ws) {
        console.log('[WebSocket] 旧连接已关闭，忽略其 onclose:', event.code)
        return
      }
      isConnectingRef.current = false
      isConnectedRef.current = false
      console.log('[WebSocket] Closed:', event.code, event.reason)
      stopHeartbeat()

      // 【产品优化】通话中网络断开：旧版立即挂断太突兀。
      // 改为给3秒重连窗口——若重连成功则通话不中断，超时仍断开才结束通话。
      const _callStore = useCallStore.getState()
      if (_callStore.status !== 'idle' && _callStore.status !== 'ended') {
        console.log('[WebSocket] 通话中网络断开，3秒重连窗口...')
        setTimeout(() => {
          const stillDisconnected = !isConnectedRef.current
          const stillInCall = useCallStore.getState().status !== 'idle' &&
            useCallStore.getState().status !== 'ended'
          if (stillDisconnected && stillInCall) {
            useCallStore.getState().endCall('网络断开，通话已结束')
          }
        }, 3000)
      }

      if (event.code === 4001 || event.code === 4003) {
        // 认证失败
        console.warn('[WebSocket] Authentication failed')
        updateConnectionStatus('auth_failed')
        clearTokens()
        return
      }

      updateConnectionStatus('disconnected')

      if (reconnectAttemptRef.current >= MAX_RECONNECT_ATTEMPTS) {
        console.warn('[WebSocket] Max reconnect attempts reached, stopping')
        return
      }

      const attempt = reconnectAttemptRef.current + 1
      const delay = Math.min(1000 * Math.pow(2, reconnectAttemptRef.current), RECONNECT_MAX_DELAY)
      const jitter = delay * 0.2 * Math.random()  // 添加随机抖动
      const actualDelay = delay + jitter

      console.log(
        `[WebSocket] Reconnecting in ${actualDelay.toFixed(0)}ms ` +
          `(attempt ${attempt}/${MAX_RECONNECT_ATTEMPTS})`
      )
      reconnectTimeoutRef.current = window.setTimeout(() => {
        reconnectAttemptRef.current++
        createWebSocket()
      }, actualDelay)
    }

    ws.onerror = (error) => {
      // stale 守卫：旧连接的错误事件忽略（关闭事件同样会被忽略）
      if (wsRef.current !== ws) return
      isConnectingRef.current = false
      isConnectedRef.current = false
      // 【断线风暴修复 20260930】不再在此改写连接状态：onerror 之后必然跟随 onclose
      // （4001 时是 auth_failed），旧版在这里 setState('disconnected') 会把即将到达的
      // auth_failed 覆盖掉，触发 App 层对 disconnected 的立即重连 → 紧循环握手风暴
      console.error('[WebSocket] Error:', error)
    }

    ws.onmessage = async (event) => {
      // stale 守卫：旧连接的迟到消息一律丢弃，避免污染当前会话状态
      if (wsRef.current !== ws) return
      lastMessageTimeRef.current = Date.now()
      // 通话二进制音频（后端 push_audio → ConnectionManager.send_bytes_to_user）
      if (event.data instanceof Blob) {
        try {
          const buf = await event.data.arrayBuffer()
          __enqueueCallAudio(buf)
        } catch (e) {
          console.warn('[WebSocket] 通话音频接收失败', e)
        }
        return
      }
      try {
        const data = JSON.parse(event.data)
        const messageText = data.text || data.content || data.data?.text || ''
        const messageId = data.id || data.timestamp || Date.now().toString()
        const actions = storeActionsRef.current
        // 获取客户端消息 ID 用于关联（后端应返回 client_message_id）
        const clientMessageId = data.client_message_id || ''

        switch (data.type) {
          case 'response_chunk': {
            // 【流式回复】增量块：追加到流式 AI 气泡（无则创建 streaming 态）
            const delta = data.delta || ''
            if (!delta || !clientMessageId) break
            const chunkPending = pendingMessageIdRef.current.get(clientMessageId)
            const chunkSessionId = chunkPending?.sessionId || actions.currentSessionId
            actions.appendStreamText(chunkSessionId, `stream_${clientMessageId}`, delta)
            break
          }

          case 'response':
          case 'greeting':
            // 【历史教训】greeting 消息每次 WS 连接（含重连）都会发送。
            // 旧版用 timestamp 分钟级去重，跨分钟/HMR 重载即失效，导致 localStorage 累积大量重复 greeting。
            // 改为按天固定 id：同一天只保留一条 greeting，彻底杜绝重复。
            const dedupId = data.type === 'greeting'
              ? `greeting_${(data.timestamp || new Date().toISOString()).slice(0, 10)}`
              : messageId
            if (checkDuplicateMessage(dedupId)) {
              break
            }
            actions.setIsTyping(false)
            // 【状态同步】回复到达时同时清思考状态（防止 thinking_status 乱序导致思考条残留）
            actions.setThinkingStatus('idle', '')
            const audioUrl = data.audio_url || data.data?.audio_url
            
            // 处理空响应 - 显示回退消息
            const finalText = messageText || '喵～本喵没听清楚，再说一次好吗？'
            
            // 【跨会话修复】回复写入用户发消息时的会话（pending 映射记录的 sessionId），
            // 而非当前会话——用户可能在等待回复期间已切换会话，旧版会把回复 push 进
            // 错误会话造成"串台"，原会话消息还会超时假失败
            const pending = clientMessageId
              ? pendingMessageIdRef.current.get(clientMessageId)
              : undefined
            const targetSessionId = pending?.sessionId || actions.currentSessionId

            actions.setMessages(targetSessionId, (prev: Message[]) => {
              // 【bug修复】prev 中已存在同 id 消息（如 localStorage 加载的 greeting
              // 与新连接收到的 greeting 同 id）时不重复添加，避免 React key 冲突与重复消息
              if (prev.some((m) => m.id === dedupId)) {
                return prev
              }
              const newMessages = [...prev]
              // 优先使用 pending 归属关联用户消息，否则使用 findIndex 查找
              let pendingIndex = -1
              if (pending) {
                pendingIndex = newMessages.findIndex((m) => m.id === pending.userMsgId)
                pendingMessageIdRef.current.delete(clientMessageId)
                
                // 清理消息超时
                actions.clearPendingTimeout(pending.userMsgId)
              } else {
                // 回退：查找第一个 sending 状态的消息
                pendingIndex = newMessages.findIndex((m) => m.status === 'sending')
                // 如果找到了，且没有 client_message_id，清理所有 sending 状态的消息
                if (pendingIndex !== -1 && !clientMessageId) {
                  console.warn('[WebSocket] 无 client_message_id，使用回退匹配逻辑')
                  // 清理超时
                  actions.clearPendingTimeout(newMessages[pendingIndex].id)
                }
              }
              
              if (pendingIndex !== -1) {
                newMessages[pendingIndex] = {
                  ...newMessages[pendingIndex],
                  status: 'done',
                }
              }

              // 【流式回复】已有流式气泡时就地收敛为最终版（不新建重复消息）
              const streamId = `stream_${clientMessageId || ''}`
              const streamIdx = clientMessageId
                ? newMessages.findIndex((m) => m.id === streamId)
                : -1
              if (streamIdx !== -1) {
                newMessages[streamIdx] = {
                  ...newMessages[streamIdx],
                  text: finalText,
                  status: 'done',
                  voiceText: data.voice_text || data.data?.voice_text || finalText,
                  audioUrl: audioUrl,
                  smallThought: data.small_thought || data.data?.small_thought,
                  thought: data.thought || data.data?.thought,
                }
                return newMessages
              }

              newMessages.push({
                id: dedupId,
                role: 'ai',
                text: finalText,
                status: 'done',
                timestamp: Date.now(),
                voiceText: data.voice_text || data.data?.voice_text || finalText,
                audioUrl: audioUrl,
                smallThought: data.small_thought || data.data?.small_thought,
                thought: data.thought || data.data?.thought,
                msgType: 'normal',
              })
              return newMessages
            })

            if (audioUrl) {
              setTimeout(() => {
                // 【通话修复】通话中不播报普通 TTS，且不调用 stopAudio——
                // 避免打断通话音频队列（push_audio）播放，也不干扰其回声抑制标志 __botSpeaking。
                const _callStatus = useCallStore.getState().status
                if (_callStatus !== 'idle' && _callStatus !== 'ended') return
                stopAudio()
                // 【首句优先】后端返回 tts_task 时说明整段已按句切分：
                // audioUrl 是首段（已先合成好，播放更快），剩余段播完后逐段拉取续播
                const ttsTask = data.tts_task || data.data?.tts_task
                if (ttsTask && ttsTask.id && ttsTask.total > 1) {
                  playAudioSequence(audioUrl, { taskId: ttsTask.id, total: ttsTask.total })
                } else {
                  playAudio(audioUrl)
                }
              }, 500)
            }
            break

          case 'thinking':
            actions.setThinkingStatus('thinking', '🤔 正在思考中...')
            break

          case 'thinking_step':
            // 思考步骤记录（用于未来扩展）
            break

          case 'thinking_status':
            // 更新思考状态
            const statusMap: Record<string, string> = {
              'thinking': 'thinking',
              'searching': 'searching', 
              'verifying': 'verifying',
              'typing': 'typing',
            }
            const step = data.content || data.status || ''
            const statusKey = statusMap[data.status] || data.status || 'thinking'
            actions.setThinkingStatus(statusKey, step)
            break

          case 'typing':
            const isTyping = data.status === 'typing' || data.status === 'start' || data.status === true
            actions.setIsTyping(isTyping)
            if (isTyping) {
              actions.setThinkingStatus('typing', '✍️ 正在打字...')
            } else {
              actions.setThinkingStatus('idle', '')
            }
            break

          case 'speech':
            break

          case 'speech_status':
            break

          case 'proactive':
            if (checkDuplicateMessage(`proactive_${messageId}`)) {
              break
            }
            actions.setMessages(actions.currentSessionId, (prev: Message[]) => {
              return [
                ...prev,
                {
                  id: `proactive_${messageId}`,
                  role: 'ai',
                  text: messageText,
                  status: 'done',
                  timestamp: Date.now(),
                  voiceText: messageText,
                  msgType: 'proactive',
                },
              ]
            })
            break

          case 'thought_bubble':
            if (checkDuplicateMessage(`bubble_${messageId}`)) {
              break
            }
            actions.setMessages(actions.currentSessionId, (prev: Message[]) => {
              return [
                ...prev,
                {
                  id: `bubble_${messageId}`,
                  role: 'ai',
                  text: '',
                  status: 'done',
                  timestamp: Date.now(),
                  smallThought: messageText,
                  msgType: 'thought_bubble',
                },
              ]
            })
            break

          case 'emotional_state':
            // 后端 _build_emotional_state 返回的字段：valence/arousal/trust/intimacy/dominant_emotion/emoji/description
            actions.setEmotionalState({
              // 使用后端返回的实际值，默认值作为回退
              valence: data.valence ?? data.data?.valence ?? 0.3,
              arousal: data.arousal ?? data.data?.arousal ?? 0.4,
              intimacy: data.intimacy ?? data.data?.intimacy ?? 0,
              trust: data.trust ?? data.data?.trust ?? 0.5,
              // 兼容旧版情感维度
              happy: data.happy ?? data.data?.happy ?? 0,
              sad: data.sad ?? data.data?.sad ?? 0,
              angry: data.angry ?? data.data?.angry ?? 0,
              love: data.love ?? data.data?.love ?? 0,
              excitement: data.excitement ?? data.data?.excitement ?? 0,
              calm: data.calm ?? data.data?.calm ?? 0,
              dominant_emotion: data.dominant_emotion ?? data.data?.dominant_emotion ?? 'content',
              dominant: data.dominant_emotion ?? data.data?.dominant_emotion ?? 'neutral',
              emoji: data.emoji ?? data.data?.emoji,
              description: data.description ?? data.data?.description,
              dominant_emoji: data.emoji ?? data.data?.emoji,
              dominant_label: data.description ?? data.data?.description,
            })
            break

          case 'growth_data':
            // 后端返回的字段直接在 data 顶层，不再嵌套在 data.data 中
            actions.setGrowthData({
              level: data.level ?? data.data?.level ?? 1,
              exp: data.exp ?? data.data?.exp ?? 0,
              expMax: data.exp_max ?? data.expMax ?? data.data?.exp_max ?? 100,
              intimacy: data.intimacy ?? data.data?.intimacy ?? 0,
              intimacyStage: data.stage ?? data.intimacyStage ?? data.data?.stage ?? '陌生人',
            })
            break

          case 'error':
            // 处理后端返回的错误
            const errorMessage = data.message || data.error || '发送失败，请重试'
            actions.setThinkingStatus('idle', '')
            actions.setIsTyping(false)

            // 通过自动重试流程处理失败（首次失败自动重试 1 次，第二次才标记 failed）
            // 【跨会话修复】按 pending 记录的原会话定位消息，而非当前会话
            if (clientMessageId) {
              const failedPending = pendingMessageIdRef.current.get(clientMessageId)
              if (failedPending) {
                // 清理超时定时器
                actions.clearPendingTimeout(failedPending.userMsgId)
                // 在原会话查找原消息文本，触发自动重试流程
                const state = useChatStore.getState()
                const msgs = state.messagesBySession.get(failedPending.sessionId) || []
                const origMsg = msgs.find(m => m.id === failedPending.userMsgId)
                if (origMsg) {
                  handleSendFailureRef.current(failedPending, origMsg.text, origMsg.replyTo)
                }
              }
            }

            // 显示错误提示
            console.error('[WebSocket] Server error:', errorMessage)
            break

          // ==================== 通话事件 ====================
          // 【通话功能修复】处理后端 CallManager 推送的通话状态变更
          // 后端通过 send_to_user 推送：{"type": "call_event", "data": {"event": "...", ...}}
          case 'call_event': {
            const callData = data.data || data
            const callEvent = callData.event || callData.status
            const callStore = useCallStore.getState()
            // 【bug修复】通话已结束/未开始时的迟到事件一律忽略：用户已挂断后，
            // 迟到的 connected/ended/ringing 事件不应把 UI 重新拉回通话状态
            // （否则通话界面突然复活、问候语音突然播放）
            if (callStore.status === 'ended' || callStore.status === 'idle') {
              break
            }
            console.log('[WebSocket] 收到通话事件:', callEvent, callData)

            if (callEvent === 'dialing') {
              callStore.setStatus('dialing')
            } else if (callEvent === 'ringing') {
              // 用户拨出后，后端响铃（outgoing 方向）
              if (callStore.direction === 'outgoing') {
                callStore.setStatus('ringing')
              }
            } else if (callEvent === 'connected') {
              callStore.setStatus('connected')
            } else if (callEvent === 'declined') {
              // 喵艺傲娇拒接
              callStore.endCall(callData.message || '喵艺拒接了你的电话…😾')
            } else if (callEvent === 'ended') {
              callStore.endCall(callData.reason || '通话结束')
            } else if (callEvent === 'missed') {
              callStore.endCall('未接来电')
            }
            break
          }

          case 'incoming_call': {
            // 喵艺主动来电 / 傲娇回拨
            const callData = data.data || data
            const reason = callData.reason || '喵艺想找你聊聊～'
            console.log('[WebSocket] 收到来电:', callData)
            useCallStore.getState().onIncoming(reason)
            break
          }

          case 'call_subtitle': {
            // 通话中实时字幕
            // 【数据对齐修复】后端 push_subtitle 传 speaker="bot"|"user"，
            // 旧代码判断 `=== 'miaoyi'` 永远为 false，导致喵艺的话被标成 user。
            // 改为直接归一：'user' → user，其余（bot/miaoyi/空）→ bot。
            const callData = data.data || data
            const rawSpeaker = callData.speaker || 'bot'
            const speaker = rawSpeaker === 'user' ? 'user' : 'bot'
            useCallStore.getState().addSubtitle({
              speaker,
              text: callData.text || '',
              ts: Date.now(),
            })
            break
          }

          case 'chat_record': {
            // 通话结束记录注入聊天流（📞 通话时长 / 📞 已拒绝 / 📞 未接来电）
            const rec = data.data || data
            const text = rec.text || ''
            if (text) {
              const chat = useChatStore.getState()
              chat.addMessage(chat.currentSessionId, {
                id: `call_record_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
                role: 'ai',
                text,
                status: 'done',
                msgType: 'normal',
                timestamp: Date.now(),
              })
            }
            break
          }

          case 'ping':
            if (wsRef.current?.readyState === WebSocket.OPEN) {
              wsRef.current.send(JSON.stringify({ type: 'pong', ts: Date.now() }))
            }
            break

          case 'pong':
            heartbeatFailureCountRef.current = 0
            break
        }
      } catch {
        console.warn('[WebSocket] Invalid message received')
      }
    }

    wsRef.current = ws

    // 【通话】暴露 __sendCallWs 供 useVoiceDialog 发送 voice_text/音频（复用主 WS，不再建第二条 callWs）
    ;(window as unknown as { __sendCallWs?: (obj: any) => void }).__sendCallWs = (obj: any) => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        // 二进制音频帧（通话后端 ASR）：直接发送 ArrayBuffer
        if (obj && obj.bytes instanceof ArrayBuffer) {
          wsRef.current.send(obj.bytes)
        } else {
          wsRef.current.send(JSON.stringify(obj))
        }
      }
    }
  }, [stopHeartbeat, startHeartbeat, checkDuplicateMessage, flushMessageQueue, updateConnectionStatus])

  // ============ 内部发送方法（通过 ref 打破循环依赖） ============

  // handleSendFailure 的引用 - 超时回调和 error 处理通过此 ref 调用
  // 实现自动重试：首次失败自动重试 1 次，第二次失败才标记为 failed
  const handleSendFailureRef = useRef<(pending: PendingEntry, text: string, replyTo?: string) => void>(() => {})

  // 通过 WebSocket 实际发送消息（不创建新消息，仅发送数据）
  const sendViaWs = useCallback((clientMessageId: string, sessionId: string, text: string, replyTo?: string, imageUrl?: string) => {
    const queuedMsg: QueuedMessage = {
      text,
      replyTo,
      ...(imageUrl ? { imageUrl } : {}),
      priority: 1,  // 用户消息高优先级
      retries: 0,
      maxRetries: 3,
      addedAt: Date.now(),
      clientMessageId,
      sessionId,
    }

    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      console.warn('[WebSocket] Not connected, queueing message')
      messageQueueRef.current.push(queuedMsg)
      persistQueue()
      return
    }

    try {
      wsRef.current.send(
        JSON.stringify({
          type: 'message',
          text,
          context: '',
          reply_to: replyTo,
          client_message_id: clientMessageId,  // 发送客户端消息 ID
          ...(imageUrl ? { image_url: imageUrl } : {}),
        })
      )
    } catch (e) {
      console.error('[WebSocket] Send failed, queueing message:', e)
      messageQueueRef.current.push(queuedMsg)
      persistQueue()
    }
  }, [persistQueue])

  // 为消息设置智能超时定时器
  // 【跨会话修复】参数为 pending 归属，超时检查在消息所属会话中进行
  const setupMessageTimeout = useCallback((pending: PendingEntry, text: string, replyTo?: string) => {
    const smartTimeout = getSmartTimeout()
    if (smartTimeout > 0) {
      const timeoutId = window.setTimeout(() => {
        // 检查消息是否仍然是 sending 状态（说明未收到响应）
        const state = useChatStore.getState()
        const messages = state.messagesBySession.get(pending.sessionId) || []
        const msg = messages.find(m => m.id === pending.userMsgId)
        if (msg && msg.status === 'sending') {
          console.warn(`[WebSocket] Message timeout (${smartTimeout}ms): ${pending.userMsgId}`)
          storeActionsRef.current.clearPendingTimeout(pending.userMsgId)
          // 触发失败处理流程（含自动重试逻辑）
          handleSendFailureRef.current(pending, text, replyTo)
        }
      }, smartTimeout)

      storeActionsRef.current.addPendingTimeout(pending.userMsgId, timeoutId)
    } else {
      // 离线时不设置超时，等待重连后自动发送
      console.log('[WebSocket] Offline mode - message will be sent when reconnected')
    }
  }, [getSmartTimeout])

  // 处理消息发送失败 - 核心自动重试逻辑
  // 首次失败：自动重试 1 次（状态改回 sending，延迟后重新发送）
  // 第二次失败：标记为 failed，显示红色感叹号，允许手动重试
  const handleSendFailure = useCallback((pending: PendingEntry, text: string, replyTo?: string) => {
    const { userMsgId, sessionId } = pending
    const retryCount = messageRetryCountRef.current.get(userMsgId) || 0

    if (retryCount < MAX_AUTO_RETRY) {
      // === 自动重试 ===
      const newRetryCount = retryCount + 1
      messageRetryCountRef.current.set(userMsgId, newRetryCount)
      console.log(`[WebSocket] Auto-retry ${newRetryCount}/${MAX_AUTO_RETRY} for message: ${userMsgId}`)

      // 将消息从失败列表移除，状态改回 sending
      storeActionsRef.current.markMessageAsRetrying(userMsgId)
      // 重新加入待处理映射（归属仍为原会话）
      pendingMessageIdRef.current.set(userMsgId, pending)

      // 延迟后重新发送（给网络恢复窗口）
      setTimeout(() => {
        sendViaWs(userMsgId, sessionId, text, replyTo)
        setupMessageTimeout(pending, text, replyTo)
      }, AUTO_RETRY_DELAY)
    } else {
      // === 达到重试上限，标记为失败 ===
      console.warn(`[WebSocket] Message failed after ${MAX_AUTO_RETRY} auto-retries: ${userMsgId}`)
      storeActionsRef.current.markMessageAsFailed(userMsgId)
      // 清理重试计数
      messageRetryCountRef.current.delete(userMsgId)
    }
  }, [sendViaWs, setupMessageTimeout])

  // 更新 ref，供超时回调和 error 处理调用
  useEffect(() => {
    handleSendFailureRef.current = handleSendFailure
  }, [handleSendFailure])

  // ============ 公开发送接口 ============

  const sendMessage = useCallback(
    (text: string, replyTo?: string, imageUrl?: string) => {
      // 【兜底】空消息（纯空白/空字符串）不发送，避免后端返回
      // "消息内容不能为空"错误并触发无意义的重试循环。
      // 【视觉能力】带图片的消息允许空文本（纯图消息）。
      const trimmed = (text || '').trim()
      if (!trimmed && !imageUrl) {
        console.warn('[WebSocket] 空消息不发送')
        return
      }
      text = trimmed
      // 【语音打断】用户发新消息 = 打断当前 AI 语音播报，立即停止并复位回声抑制标志
      stopAudio()
      // 生成唯一的客户端消息 ID
      const clientMessageId = `msg_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`

      const newMessage: Message = {
        id: clientMessageId,  // 使用 clientMessageId 作为消息 ID
        role: 'user',
        text,
        status: 'sending',
        timestamp: Date.now(),
        replyTo,
        ...(imageUrl ? { msgType: 'image' as const, imageUrl } : {}),
      }

      // 【跨会话修复】记录消息归属（当前会话），回复到达时据此路由
      const sessionId = storeActionsRef.current.currentSessionId
      pendingMessageIdRef.current.set(clientMessageId, { userMsgId: clientMessageId, sessionId })
      // 初始化重试计数为 0
      messageRetryCountRef.current.set(clientMessageId, 0)

      storeActionsRef.current.addMessage(sessionId, newMessage)

      // 通过 WebSocket 发送 + 设置超时
      sendViaWs(clientMessageId, sessionId, text, replyTo, imageUrl)
      setupMessageTimeout({ userMsgId: clientMessageId, sessionId }, text, replyTo)
    },
    [sendViaWs, setupMessageTimeout, stopAudio]
  )

  // 手动重试失败的消息 - 重置重试计数，重新发送
  const retryMessage = useCallback(
    (messageId: string, text: string, replyTo?: string) => {
      // 【兜底】空消息不重试
      const trimmed = (text || '').trim()
      if (!trimmed) {
        console.warn('[WebSocket] 空消息不重试')
        return
      }
      console.log(`[WebSocket] Manual retry for message: ${messageId}`)
      // 【跨会话修复】跨全部会话反查该消息归属（失败消息可能不在当前会话）
      const currentState = useChatStore.getState()
      let sessionId = currentState.currentSessionId
      currentState.messagesBySession.forEach((msgs, sid) => {
        if (msgs.some((m) => m.id === messageId)) sessionId = sid
      })
      const pending = { userMsgId: messageId, sessionId }
      // 重置自动重试计数，给用户手动重试也留一次自动重试机会
      messageRetryCountRef.current.set(messageId, 0)
      // 将消息状态从 failed 改回 sending
      storeActionsRef.current.markMessageAsRetrying(messageId)
      // 重新加入待处理映射
      pendingMessageIdRef.current.set(messageId, pending)
      // 重新发送
      sendViaWs(messageId, sessionId, text, replyTo)
      setupMessageTimeout(pending, text, replyTo)
    },
    [sendViaWs, setupMessageTimeout]
  )

  const reconnect = useCallback(() => {
    // 【打断修复】连接健康时跳过重连：online/visibility 监听可能在已连接时触发，
    // 旧版无条件 close 健康连接，会打断正在进行的 AI 回复并清空队列
    if (isConnectedRef.current && wsRef.current?.readyState === WebSocket.OPEN) return
    // 正在连接中也不重复创建（createWebSocket 内部有守卫，这里提前退出避免多余操作）
    if (isConnectingRef.current) return
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current)
      reconnectTimeoutRef.current = null
    }
    reconnectAttemptRef.current = 0
    // 【队列丢失修复】不再 messageQueueRef.current = []：离线/弱网已入队消息必须保留，
    // createWebSocket 连接成功后 flush 会把它们发出；旧版清空队列使消息永久卡 sending
    createWebSocket()
  }, [createWebSocket])

  // ============ 初始化 ============

  // 【#13 修复】监听器 + 队列恢复与登录态无关，无条件注册一次：
  // 旧版在无 token 时整个 effect 提前 return，导致"未登录启动→登录成功"后
  // online/offline/visibility 监听与队列定时保存在本次运行期永久缺失
  useEffect(() => {
    loadQueueFromStorage()

    const handleOnline = () => {
      console.log('[WebSocket] Network online, reconnecting...')
      if (!isConnectedRef.current) {
        // 记录重连时间
        const reconnectTime = Date.now()
        
        // 使用延迟确保网络稳定后再重连
        setTimeout(() => {
          if (!isConnectedRef.current && getAccessToken()) {
            console.log(`[WebSocket] Reconnecting after network stable (${Date.now() - reconnectTime}ms)`)
            reconnect()
          }
        }, 500)  // 500ms 延迟等待网络稳定
      }
    }

    const handleOffline = () => {
      console.log('[WebSocket] Network offline')
      updateConnectionStatus('disconnected')
      stopHeartbeat()
      
      // 离线时将所有发送中的消息重新加入队列
      const state = useChatStore.getState()
      const messages = state.messagesBySession.get(state.currentSessionId) || []
      const sendingMessages = messages.filter(m => m.status === 'sending')
      
      if (sendingMessages.length > 0) {
        console.warn(`[WebSocket] ${sendingMessages.length} 条发送中的消息重新加入队列`)
        sendingMessages.forEach(msg => {
          // 【重复入队修复】sendViaWs 未连接时已入过队，按 clientMessageId 去重
          if (messageQueueRef.current.some(q => q.clientMessageId === msg.id)) return
          // 添加到消息队列（带上会话归属）
          messageQueueRef.current.push({
            text: msg.text,
            replyTo: msg.replyTo,
            priority: 1,  // 高优先级
            retries: 0,
            maxRetries: 3,
            addedAt: Date.now(),
            clientMessageId: msg.id,
            sessionId: state.currentSessionId,
          })
        })
        persistQueue()
      }
    }

    const handleVisibilityChange = () => {
      // 页面可见性变化时检查连接
      if (document.visibilityState === 'visible' && !isConnectedRef.current) {
        console.log('[WebSocket] Page visible, checking connection...')
        if (getAccessToken()) {
          reconnect()
        }
      }
    }

    window.addEventListener('online', handleOnline)
    window.addEventListener('offline', handleOffline)
    document.addEventListener('visibilitychange', handleVisibilityChange)

    // 定期保存队列
    const queueSaveInterval = window.setInterval(() => {
      if (messageQueueRef.current.length > 0) {
        persistQueue()
      }
    }, 5000)

    return () => {
      window.removeEventListener('online', handleOnline)
      window.removeEventListener('offline', handleOffline)
      document.removeEventListener('visibilitychange', handleVisibilityChange)
      window.clearInterval(queueSaveInterval)
    }
  }, [reconnect, loadQueueFromStorage, persistQueue, stopHeartbeat, updateConnectionStatus])

  // 【#13 修复】连接建立按登录态独立处理；登录成功后 App.handleLogin 也会显式 reconnect
  useEffect(() => {
    const token = getAccessToken()
    // 严格 token 校验（与 createWebSocket 一致）：空/undefined/纯空白 都视为未登录
    if (!token || typeof token !== 'string' || token.trim().length === 0) {
      console.log('[WebSocket] 未检测到有效 token，等待登录后再连接...')
      updateConnectionStatus('disconnected')
      return
    }

    createWebSocket()
  }, [createWebSocket, updateConnectionStatus])

  // 卸载时清理连接（先摘引用再关闭，避免触发状态写入）
  useEffect(() => {
    return () => {
      stopHeartbeat()
      const ws = wsRef.current
      wsRef.current = null
      if (ws) {
        try { ws.close() } catch { /* ignore */ }
      }
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current)
        reconnectTimeoutRef.current = null
      }
    }
  }, [stopHeartbeat])

  return {
    sendMessage,
    reconnect,
    retryMessage,
    connectionStatus,
    isConnected: isConnectedRef.current,
  }
}
