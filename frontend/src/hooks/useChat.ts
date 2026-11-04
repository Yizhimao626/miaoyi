import { useState, useRef, useCallback, useEffect } from 'react'
import { useChatStore } from '../store/chatStore'
import { getAccessToken, getValidToken, refreshAuthToken } from '../utils/auth'
import { useNetwork } from './useNetwork'
import { getBaseUrl } from '../utils/config'
import type { Message } from '../types'

const MIN_SEND_INTERVAL = 500
const OFFLINE_QUEUE_KEY = 'miaoyi_offline_queue'
const DEDUP_CACHE_SIZE = 200
const MAX_OFFLINE_RETRIES = 3
const OFFLINE_MSG_TTL = 5 * 60 * 1000

// SSE 事件接口 - 与后端保持一致的事件格式
interface SSEEvent {
  type: string
  content?: string
  token?: string
  is_last?: boolean
  progress?: number
  emotional_state?: Record<string, any>
  small_thought?: string
  thought?: string
  status?: string
  error?: string
}

// 离线队列消息接口
interface OfflineMessage {
  id: string
  text: string
  replyTo?: string
  timestamp: number
  retries: number
}

// Hook 返回接口
interface UseChatReturn {
  sendMessage: (text: string, replyTo?: string) => void
  streamChat: (userId: string, text: string, replyTo?: string) => Promise<void>
  isStreaming: boolean
  streamText: string
  cancelStream: () => void
  retryMessage: (messageId: string, text: string, replyTo?: string) => void
  reconnect: () => Promise<void>
}

export function useChat(): UseChatReturn {
  // ============ React State ============
  const [isStreaming, setIsStreaming] = useState(false)
  const [streamText, setStreamText] = useState('')

  // ============ Refs（避免不必要的重渲染） ============
  const abortControllerRef = useRef<AbortController | null>(null)
  const lastSendTimeRef = useRef(0)
  const sentMessageIdsRef = useRef<Set<string>>(new Set())
  const offlineQueueRef = useRef<OfflineMessage[]>([])
  const isStreamingRef = useRef(false)
  const sessionIdRef = useRef('default')

  // ============ Hooks ============
  const { isOnline } = useNetwork()

  // 从 chatStore 获取方法（使用 ref 避免依赖地狱）
  const {
    addMessage,
    updateMessage,
    setMessages,
    getMessages,
    currentSessionId,
  } = useChatStore()

  useEffect(() => {
    sessionIdRef.current = currentSessionId
  }, [currentSessionId])

  // ============ 消息去重 ============
  const isDuplicate = useCallback((messageId: string): boolean => {
    if (sentMessageIdsRef.current.has(messageId)) {
      return true
    }
    sentMessageIdsRef.current.add(messageId)
    if (sentMessageIdsRef.current.size > DEDUP_CACHE_SIZE) {
      const first = sentMessageIdsRef.current.values().next().value
      if (first) sentMessageIdsRef.current.delete(first)
    }
    return false
  }, [])

  // ============ 离线队列管理 ============

  const loadOfflineQueue = useCallback((): void => {
    try {
      const stored = localStorage.getItem(OFFLINE_QUEUE_KEY)
      if (stored) {
        offlineQueueRef.current = JSON.parse(stored)
      }
    } catch {
      // localStorage 可能被禁用
    }
  }, [])

  const persistOfflineQueue = useCallback((): void => {
    try {
      if (offlineQueueRef.current.length > 0) {
        localStorage.setItem(OFFLINE_QUEUE_KEY, JSON.stringify(offlineQueueRef.current))
      } else {
        localStorage.removeItem(OFFLINE_QUEUE_KEY)
      }
    } catch {
      // 忽略
    }
  }, [])

  const addToOfflineQueue = useCallback((text: string, replyTo?: string): void => {
    // 防止重复添加相同消息
    const isDuplicateText = offlineQueueRef.current.some(
      m => m.text === text && Date.now() - m.timestamp < 30000
    )
    if (isDuplicateText) return

    const offlineMsg: OfflineMessage = {
      id: `offline_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
      text,
      replyTo,
      timestamp: Date.now(),
      retries: 0,
    }
    offlineQueueRef.current.push(offlineMsg)
    persistOfflineQueue()
    console.log(`[useChat] 消息已加入离线队列 (${offlineQueueRef.current.length} 条待发送)`)
  }, [persistOfflineQueue])

  const flushOfflineQueue = useCallback(async (): Promise<void> => {
    if (offlineQueueRef.current.length === 0) return

    const now = Date.now()
    const validMessages: OfflineMessage[] = []
    const expiredMessages: OfflineMessage[] = []

    // 分离过期消息
    for (const msg of offlineQueueRef.current) {
      if (now - msg.timestamp > OFFLINE_MSG_TTL) {
        expiredMessages.push(msg)
      } else {
        validMessages.push(msg)
      }
    }

    // 处理过期消息
    for (const expired of expiredMessages) {
      console.warn(`[useChat] 离线消息已过期，丢弃: ${expired.text}`)
    }

    offlineQueueRef.current = validMessages
    persistOfflineQueue()

    // 逐条发送有效消息
    const remaining: OfflineMessage[] = []
    for (const msg of offlineQueueRef.current) {
      try {
        await streamChatInternal(sessionIdRef.current, msg.text, msg.replyTo)
      } catch {
        msg.retries++
        if (msg.retries < MAX_OFFLINE_RETRIES) {
          remaining.push(msg)
        } else {
          console.warn(`[useChat] 离线消息重试次数耗尽，丢弃: ${msg.text}`)
        }
      }
    }

    offlineQueueRef.current = remaining
    persistOfflineQueue()

    if (remaining.length > 0) {
      console.warn(`[useChat] ${remaining.length} 条离线消息仍待重试`)
    }
  }, [persistOfflineQueue])

  // ============ 速率限制 ============
  const checkRateLimit = useCallback((): boolean => {
    const now = Date.now()
    if (now - lastSendTimeRef.current < MIN_SEND_INTERVAL) {
      return false
    }
    lastSendTimeRef.current = now
    return true
  }, [])

  // ============ SSE 流解析核心方法 ============

  /**
   * 解析 SSE 流响应
   * 处理 token / done / error 等事件
   */
  const parseSSEStream = useCallback(async (
    response: Response,
    controller: AbortController,
    userMessageId: string,
  ): Promise<void> => {
    const reader = response.body?.getReader()
    if (!reader) {
      throw new Error('No response body')
    }

    const decoder = new TextDecoder()
    let buffer = ''
    const aiMessageId = `ai_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`
    let firstChunk = true
    let smallThought: string | null = null
    let thought: string | null = null

    while (true) {
      if (controller.signal.aborted) break

      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue

        try {
          const jsonStr = line.slice(6)
          if (jsonStr === '[DONE]') continue

          const event: SSEEvent = JSON.parse(jsonStr)

          switch (event.type) {
            case 'token': {
              const content = event.content || event.token || ''
              setStreamText(prev => prev + content)

              if (firstChunk) {
                firstChunk = false
                smallThought = event.small_thought || null
                thought = event.thought || null

                // 第一条 token：创建 AI 消息
                const aiMessage: Message = {
                  id: aiMessageId,
                  role: 'ai',
                  text: content,
                  status: 'typing',
                  timestamp: Date.now(),
                  msgType: 'normal',
                  smallThought: smallThought || undefined,
                  thought: thought || undefined,
                }
                addMessage(sessionIdRef.current, aiMessage)
              } else {
                // 后续 token：追加到已有 AI 消息
                const messages = getMessages(sessionIdRef.current)
                const aiMsgIndex = messages.findIndex(m => m.id === aiMessageId)

                if (aiMsgIndex !== -1) {
                  const updatedMessages = [...messages]
                  updatedMessages[aiMsgIndex] = {
                    ...updatedMessages[aiMsgIndex],
                    text: updatedMessages[aiMsgIndex].text + content,
                    status: event.is_last ? 'done' : 'typing',
                  }
                  setMessages(sessionIdRef.current, updatedMessages)
                }
              }

              if (event.is_last) {
                updateMessage(sessionIdRef.current, aiMessageId, { status: 'done' })
              }
              break
            }

            case 'small_thought': {
              if (firstChunk) {
                smallThought = event.content || ''
              }
              break
            }

            case 'thought': {
              if (firstChunk) {
                thought = event.content || ''
              }
              break
            }

            case 'done': {
              updateMessage(sessionIdRef.current, aiMessageId, { status: 'done' })
              break
            }

            case 'error': {
              console.error('[useChat] SSE error:', event.error)
              updateMessage(sessionIdRef.current, aiMessageId, { status: 'failed' })
              break
            }

            case 'thinking_status':
            case 'typing':
            case 'searching':
            case 'verifying':
              // 思考状态事件，由外层 chatStore 状态管理处理
              break

            default:
              break
          }
        } catch (e) {
          console.warn('[useChat] SSE parse error:', e)
        }
      }
    }

    // 流正常结束：确保消息状态正确
    if (!controller.signal.aborted) {
      const messages = getMessages(sessionIdRef.current)
      const aiMsg = messages.find(m => m.id === aiMessageId)
      if (aiMsg && aiMsg.status === 'typing') {
        updateMessage(sessionIdRef.current, aiMessageId, { status: 'done' })
      }
      updateMessage(sessionIdRef.current, userMessageId, { status: 'done' })
    }
  }, [addMessage, updateMessage, setMessages, getMessages])

  // ============ SSE 流式聊天核心方法 ============

  /**
   * 内部流式聊天实现（可被 sendMessage 和 flushOfflineQueue 调用）
   */
  const streamChatInternal = useCallback(async (
    userId: string,
    text: string,
    replyTo?: string,
  ): Promise<void> => {
    if (isStreamingRef.current) {
      console.warn('[useChat] 正在流式传输中，忽略新请求')
      return
    }

    if (!text.trim()) return

    isStreamingRef.current = true
    setIsStreaming(true)
    setStreamText('')

    // 取消之前的请求
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
    }

    const controller = new AbortController()
    abortControllerRef.current = controller

    const userMessageId = `msg_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`

    // 去重
    if (isDuplicate(userMessageId)) {
      isStreamingRef.current = false
      setIsStreaming(false)
      return
    }

    // 创建用户消息到 chatStore
    const userMessage: Message = {
      id: userMessageId,
      role: 'user',
      text,
      status: 'sending',
      timestamp: Date.now(),
      replyTo,
    }
    addMessage(sessionIdRef.current, userMessage)

    try {
      const token = getAccessToken()
      const response = await fetch(`${getBaseUrl()}/v1/chat/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': token ? `Bearer ${token}` : '',
        },
        body: JSON.stringify({
          user_id: userId,
          text,
          reply_to: replyTo,
        }),
        signal: controller.signal,
      })

      // 401 处理：Token 过期，自动刷新后重试
      if (response.status === 401) {
        const newToken = await refreshAuthToken()
        if (newToken) {
          const retryResponse = await fetch(`${getBaseUrl()}/chat/stream`, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'Authorization': `Bearer ${newToken}`,
            },
            body: JSON.stringify({
              user_id: userId,
              text,
              reply_to: replyTo,
            }),
            signal: controller.signal,
          })

          if (!retryResponse.ok) {
            throw new Error(`Token 刷新后请求仍失败: ${retryResponse.status}`)
          }
          await parseSSEStream(retryResponse, controller, userMessageId)
          return
        } else {
          throw new Error('认证已过期，请重新登录')
        }
      }

      // 429 处理：速率限制
      if (response.status === 429) {
        throw new Error('请求过于频繁，请稍后再试')
      }

      // 其他 HTTP 错误
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`)
      }

      // 正常响应：解析 SSE 流
      await parseSSEStream(response, controller, userMessageId)
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        console.log('[useChat] 流已被用户取消')
        updateMessage(sessionIdRef.current, userMessageId, { status: 'failed' })
      } else {
        console.error('[useChat] 流请求失败:', error)
        updateMessage(sessionIdRef.current, userMessageId, { status: 'failed' })

        // 添加错误提示消息
        const errorAiMessage: Message = {
          id: `error_${Date.now()}`,
          role: 'ai',
          text: '抱歉，网络连接出现问题，请稍后重试。',
          status: 'failed',
          timestamp: Date.now(),
        }
        addMessage(sessionIdRef.current, errorAiMessage)

        // 离线时加入队列等待网络恢复
        if (!isOnline) {
          addToOfflineQueue(text, replyTo)
        }
      }
    } finally {
      isStreamingRef.current = false
      setIsStreaming(false)
      abortControllerRef.current = null
    }
  }, [isDuplicate, addMessage, updateMessage, isOnline, addToOfflineQueue, parseSSEStream])

  // ============ 公开 API ============

  /**
   * 通过 WebSocket 发送消息（现有方式）
   * 保留此方法以兼容现有调用方
   */
  const sendMessage = useCallback((text: string, replyTo?: string): void => {
    if (!checkRateLimit()) {
      console.warn('[useChat] 发送过于频繁，已被速率限制 (500ms)')
      return
    }

    const messageId = `msg_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`

    if (isDuplicate(messageId)) {
      console.warn('[useChat] 重复消息，已跳过')
      return
    }

    const userMessage: Message = {
      id: messageId,
      role: 'user',
      text,
      status: 'sending',
      timestamp: Date.now(),
      replyTo,
    }

    addMessage(sessionIdRef.current, userMessage)

    if (!isOnline) {
      addToOfflineQueue(text, replyTo)
      console.warn('[useChat] 离线状态，消息已加入队列')
      return
    }

    // 标记为 sending，实际发送由 useWebSocket 处理
    // useWebSocket 会监听 chatStore 中 sending 状态的消息
  }, [checkRateLimit, isDuplicate, addMessage, isOnline, addToOfflineQueue])

  /**
   * 流式聊天（SSE 方式）
   * 移动端推荐使用此方法以获得更好的交互体验
   */
  const streamChat = useCallback(async (
    userId: string,
    text: string,
    replyTo?: string,
  ): Promise<void> => {
    await streamChatInternal(userId, text, replyTo)
  }, [streamChatInternal])

  /**
   * 取消当前正在进行的流式传输
   */
  const cancelStream = useCallback((): void => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
      abortControllerRef.current = null
    }
    isStreamingRef.current = false
    setIsStreaming(false)
  }, [])

  /**
   * 重试失败的消息
   */
  const retryMessage = useCallback((messageId: string, text: string, replyTo?: string): void => {
    if (!checkRateLimit()) {
      console.warn('[useChat] 重试过于频繁')
      return
    }

    updateMessage(sessionIdRef.current, messageId, {
      status: 'sending',
      timestamp: Date.now(),
    })

    if (isOnline) {
      streamChatInternal(sessionIdRef.current, text, replyTo)
    } else {
      addToOfflineQueue(text, replyTo)
    }
  }, [checkRateLimit, updateMessage, isOnline, streamChatInternal, addToOfflineQueue])

  /**
   * 重连：刷新 token + 冲刷离线队列
   */
  const reconnect = useCallback(async (): Promise<void> => {
    try {
      const newToken = await getValidToken()
      if (!newToken) {
        console.warn('[useChat] 无法刷新 token，需要重新登录')
        return
      }
      console.log('[useChat] Token 刷新成功')
    } catch {
      console.warn('[useChat] Token 无效')
      return
    }

    if (isOnline && offlineQueueRef.current.length > 0) {
      console.log(`[useChat] 重连后冲刷 ${offlineQueueRef.current.length} 条离线消息`)
      await flushOfflineQueue()
    }
  }, [isOnline, flushOfflineQueue])

  // ============ 生命周期管理 ============

  // 网络恢复时自动冲刷离线队列
  useEffect(() => {
    const handleOnline = () => {
      console.log('[useChat] 网络恢复，冲刷离线队列')
      flushOfflineQueue()
    }

    window.addEventListener('online', handleOnline)
    return () => {
      window.removeEventListener('online', handleOnline)
    }
  }, [flushOfflineQueue])

  // 初始化：加载离线队列
  useEffect(() => {
    loadOfflineQueue()
  }, [loadOfflineQueue])

  // 组件卸载时清理
  useEffect(() => {
    return () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort()
      }
    }
  }, [])

  return {
    sendMessage,
    streamChat,
    isStreaming,
    streamText,
    cancelStream,
    retryMessage,
    reconnect,
  }
}