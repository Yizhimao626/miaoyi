import { create } from 'zustand'
import type { Message, EmotionalState, GrowthData } from '../types'

let saveTimeout: ReturnType<typeof setTimeout> | null = null

const debouncedSave = (saveFn: () => void): void => {
  if (saveTimeout) {
    clearTimeout(saveTimeout)
  }
  saveTimeout = setTimeout(() => {
    saveFn()
    saveTimeout = null
  }, 500)
}

interface ChatState {
  messagesBySession: Map<string, Message[]>
  currentSessionId: string
  connectionStatus: 'connecting' | 'connected' | 'disconnected' | 'auth_failed'
  emotionalState: EmotionalState
  growthData: GrowthData
  isTyping: boolean
  // 思考状态
  thinkingStatus: string  // 当前思考状态：'idle' | 'thinking' | 'searching' | 'verifying' | 'typing'
  thinkingStep: string    // 当前思考步骤描述
  thinkingProgress: number  // 思考进度 0-100
  thinkingStepIndex: number  // 当前步骤索引
  thinkingTotalSteps: number  // 总步骤数
  // 消息超时管理
  pendingTimeouts: Map<string, number>  // messageId -> timeoutId
  // 批量重试
  failedMessageIds: string[]  // 失败消息 ID 列表
  
  setCurrentSessionId: (sessionId: string) => void
  
  getMessages: (sessionId: string) => Message[]
  
  setMessages: (sessionId: string, messages: Message[] | ((prev: Message[]) => Message[])) => void
  
  addMessage: (sessionId: string, message: Message) => void
  
  updateMessage: (sessionId: string, id: string, updates: Partial<Message>) => void
  
  removeMessage: (sessionId: string, id: string) => void
  
  clearMessages: (sessionId: string) => void
  
  setConnectionStatus: (status: ChatState['connectionStatus']) => void
  
  setEmotionalState: (state: EmotionalState) => void
  
  setGrowthData: (data: GrowthData) => void
  
  setIsTyping: (typing: boolean) => void
  
  setThinkingStatus: (status: string, step?: string) => void
  
  setThinkingProgress: (progress: number, stepIndex?: number, totalSteps?: number) => void
  
  addPendingTimeout: (messageId: string, timeoutId: number) => void
  
  clearPendingTimeout: (messageId: string) => void
  
  markMessageAsFailed: (messageId: string) => void

  // 将消息从失败列表移除并重新标记为发送中（用于自动重试/手动重试）
  markMessageAsRetrying: (messageId: string) => void

  retryAllFailed: (sendFn: (text: string) => void) => void
  
  initSessionMessages: (sessionId: string, messages: Message[]) => void
  
  mergeMessages: (sessionId: string, messages: Message[]) => void
  
  markMessageAsDone: (sessionId: string, messageId: string) => void

  // 【流式回复】追加增量文本：无该消息则创建 streaming 态 AI 气泡，有则拼接
  appendStreamText: (sessionId: string, messageId: string, delta: string) => void

  // 标记会话中所有 AI 消息为已读（用户进入会话时调用）
  markSessionAsRead: (sessionId: string) => void

  loadFromStorage: () => void
  
  saveToStorage: () => void

  // 【#6 隐私修复】清空全部会话/消息状态（退出登录时调用），防止换账号后
  // 下一任用户看到上一任的聊天记录
  resetStore: () => void
}

const STORAGE_KEY = 'miaoyi-chat-store'
const MAX_STORAGE_SIZE = 5 * 1024 * 1024
const MAX_MESSAGES_PER_SESSION = 200

const estimateSize = (obj: any): number => {
  return new Blob([JSON.stringify(obj)]).size
}

const cleanupOldMessages = (messagesBySession: Map<string, Message[]>): Map<string, Message[]> => {
  const newMap = new Map(messagesBySession)
  
  newMap.forEach((messages, sessionId) => {
    if (messages.length > MAX_MESSAGES_PER_SESSION) {
      const keepCount = Math.floor(MAX_MESSAGES_PER_SESSION * 0.8)
      const newMessages = messages.slice(-keepCount)
      newMap.set(sessionId, newMessages)
      console.log(`[chatStore] Cleaned up ${messages.length - keepCount} old messages for session ${sessionId}`)
    }
  })
  
  return newMap
}

const mapToObject = (map: Map<string, Message[]>): Record<string, Message[]> => {
  const obj: Record<string, Message[]> = {}
  map.forEach((value, key) => {
    obj[key] = value
  })
  return obj
}

const objectToMap = (obj: Record<string, Message[]>): Map<string, Message[]> => {
  return new Map(Object.entries(obj))
}

const loadFromStorage = (): Partial<ChatState> => {
  try {
    const data = localStorage.getItem(STORAGE_KEY)
    if (data) {
      const parsed = JSON.parse(data)
      let messagesMap = parsed.messagesBySession ? objectToMap(parsed.messagesBySession) : new Map()
      // 【历史数据清理】旧版 greeting 去重失效，localStorage 可能累积大量重复欢迎消息。
      // 加载时按 id 去重（保留最后一条），并清理所有 id 以 greeting_ 开头的重复项（每天只留一条）。
      messagesMap = new Map(
        Array.from(messagesMap.entries()).map(([sid, msgs]) => {
          const seen = new Set<string>()
          const deduped = (msgs as Message[]).filter((m) => {
            if (seen.has(m.id)) return false
            seen.add(m.id)
            return true
          })
          // greeting 消息：同一天只保留最后一条
          const greetingIds = new Set(
            deduped.filter((m) => m.id?.startsWith('greeting_')).map((m) => m.id)
          )
          const keptGreetings = new Set<string>()
          const cleaned = deduped.filter((m) => {
            if (greetingIds.has(m.id)) {
              if (keptGreetings.has(m.id)) return false // 重复的 greeting 移除
              keptGreetings.add(m.id)
            }
            return true
          })
          return [sid, cleaned]
        })
      )
      return {
        messagesBySession: messagesMap,
        currentSessionId: parsed.currentSessionId || 'default',
        emotionalState: parsed.emotionalState || { valence: 0.3, arousal: 0.4, intimacy: 0.5, trust: 0.5, dominant_emotion: 'content', dominant_label: '满足', dominant_emoji: '😌' },
        growthData: parsed.growthData || { level: 1, exp: 0, expMax: 100, intimacy: 0, intimacyStage: '初识' },
        // 恢复思考状态 - 用于断线恢复
        thinkingStatus: parsed.thinkingStatus || 'idle',
        thinkingStep: parsed.thinkingStep || '',
        thinkingProgress: parsed.thinkingProgress || 0,
        thinkingStepIndex: parsed.thinkingStepIndex || 0,
        // 恢复失败消息列表
        failedMessageIds: parsed.failedMessageIds || [],
      }
    }
  } catch {
    console.warn('Failed to load chat store from storage')
  }
  return {}
}

const saveToStorage = (state: ChatState): void => {
  try {
    let dataToSave = {
      messagesBySession: mapToObject(state.messagesBySession),
      currentSessionId: state.currentSessionId,
      emotionalState: state.emotionalState,
      growthData: state.growthData,
      // 保存思考状态 - 用于断线恢复
      thinkingStatus: state.thinkingStatus,
      thinkingStep: state.thinkingStep,
      thinkingProgress: state.thinkingProgress,
      thinkingStepIndex: state.thinkingStepIndex,
      // 保存失败消息列表
      failedMessageIds: state.failedMessageIds,
    }
    
    let size = estimateSize(dataToSave)
    
    if (size > MAX_STORAGE_SIZE) {
      console.warn(`[chatStore] Storage size exceeded (${size} bytes), cleaning up...`)
      
      let cleanedMap = cleanupOldMessages(state.messagesBySession)
      dataToSave.messagesBySession = mapToObject(cleanedMap)
      
      size = estimateSize(dataToSave)
      if (size > MAX_STORAGE_SIZE) {
        console.warn(`[chatStore] Still too large, removing oldest sessions...`)
        const entries: [string, Message[]][] = Object.entries(dataToSave.messagesBySession)
        const sortedSessions = entries.sort((a, b) => {
          const aTime = a[1].length > 0 ? a[1][0].timestamp : 0
          const bTime = b[1].length > 0 ? b[1][0].timestamp : 0
          return aTime - bTime
        })
        
        while (size > MAX_STORAGE_SIZE && sortedSessions.length > 1) {
          const oldest = sortedSessions.shift()
          if (oldest) {
            delete dataToSave.messagesBySession[oldest[0]]
            size = estimateSize(dataToSave)
            console.log(`[chatStore] Removed oldest session: ${oldest[0]}`)
          }
        }
      }
    }
    
    localStorage.setItem(STORAGE_KEY, JSON.stringify(dataToSave))
  } catch (e) {
    console.warn('Failed to save chat store to storage:', e)
  }
}

const initialData = loadFromStorage()

// 断线恢复处理 - 检查并修正未完成的状态
const recoverFromDisconnect = (data: Partial<ChatState>): Partial<ChatState> => {
  const result = { ...data }
  
  // 1. 检查思考状态 - 如果不是 idle，重置为 idle
  if (result.thinkingStatus && result.thinkingStatus !== 'idle') {
    console.log('[chatStore] Recovering from disconnect - resetting thinking status')
    result.thinkingStatus = 'idle'
    result.thinkingStep = ''
    result.thinkingProgress = 0
    result.thinkingStepIndex = 0
  }
  
  // 2. 检查发送中的消息 - 标记为失败
  if (result.messagesBySession) {
    const newMap = new Map<string, Message[]>()
    const failedIds: string[] = result.failedMessageIds || []
    
    result.messagesBySession.forEach((messages, sessionId) => {
      const recoveredMessages = messages.map(msg => {
        if (msg.status === 'sending') {
          console.log(`[chatStore] Recovering sending message: ${msg.id}`)
          if (!failedIds.includes(msg.id)) {
            failedIds.push(msg.id)
          }
          return { ...msg, status: 'failed' as const }
        }
        return msg
      })
      newMap.set(sessionId, recoveredMessages)
    })
    
    result.messagesBySession = newMap
    result.failedMessageIds = failedIds
  }
  
  return result
}

const recoveredData = recoverFromDisconnect(initialData)

export const useChatStore = create<ChatState>((set, get) => ({
  messagesBySession: recoveredData.messagesBySession || new Map(),
  currentSessionId: recoveredData.currentSessionId || 'default',
  connectionStatus: 'connecting',
  emotionalState: recoveredData.emotionalState || { valence: 0.3, arousal: 0.4, intimacy: 0.5, trust: 0.5, dominant_emotion: 'content', dominant_label: '满足', dominant_emoji: '😌' },
  growthData: recoveredData.growthData || { level: 1, exp: 0, expMax: 100, intimacy: 0, intimacyStage: '初识' },
  isTyping: false,
  thinkingStatus: 'idle',  // 默认空闲状态 - 断线恢复后重置
  thinkingStep: '',
  thinkingProgress: 0,
  thinkingStepIndex: 0,
  thinkingTotalSteps: 4,
  pendingTimeouts: new Map(),
  failedMessageIds: recoveredData.failedMessageIds || [],

  setCurrentSessionId: (sessionId) => {
    set({ currentSessionId: sessionId })
    debouncedSave(() => saveToStorage(get()))
  },

  getMessages: (sessionId) => {
    return get().messagesBySession.get(sessionId) || []
  },

  setMessages: (sessionId, messages) => {
    set((state) => {
      const newMap = new Map(state.messagesBySession)
      const currentMessages = newMap.get(sessionId) || []
      if (typeof messages === 'function') {
        newMap.set(sessionId, messages(currentMessages))
      } else {
        newMap.set(sessionId, messages)
      }
      return { messagesBySession: newMap }
    })
    debouncedSave(() => saveToStorage(get()))
  },

  addMessage: (sessionId, message) => {
    set((state) => {
      const newMap = new Map(state.messagesBySession)
      const currentMessages = newMap.get(sessionId) || []
      const existingIds = new Set(currentMessages.map((m) => m.id))
      
      if (existingIds.has(message.id)) {
        console.warn(`[chatStore] Duplicate message detected, skipping: ${message.id}`)
        return state
      }
      
      newMap.set(sessionId, [...currentMessages, message])
      return { messagesBySession: newMap }
    })
    debouncedSave(() => saveToStorage(get()))
  },

  updateMessage: (sessionId, id, updates) => {
    set((state) => {
      const newMap = new Map(state.messagesBySession)
      const currentMessages = newMap.get(sessionId) || []
      newMap.set(
        sessionId,
        currentMessages.map((m) => (m.id === id ? { ...m, ...updates } : m))
      )
      return { messagesBySession: newMap }
    })
    debouncedSave(() => saveToStorage(get()))
  },

  removeMessage: (sessionId, id) => {
    set((state) => {
      const newMap = new Map(state.messagesBySession)
      const currentMessages = newMap.get(sessionId) || []
      newMap.set(sessionId, currentMessages.filter((m) => m.id !== id))
      return { messagesBySession: newMap }
    })
    debouncedSave(() => saveToStorage(get()))
  },

  clearMessages: (sessionId) => {
    set((state) => {
      const newMap = new Map(state.messagesBySession)
      newMap.set(sessionId, [])
      return { messagesBySession: newMap }
    })
    debouncedSave(() => saveToStorage(get()))
  },

  setConnectionStatus: (status) => set({ connectionStatus: status }),
  
  setEmotionalState: (state) => {
    set({ emotionalState: state })
    debouncedSave(() => saveToStorage(get()))
  },
  
  setGrowthData: (data) => {
    set({ growthData: data })
    debouncedSave(() => saveToStorage(get()))
  },
  
  setIsTyping: (typing) => set({ isTyping: typing }),
  
  setThinkingStatus: (status, step?) => {
    // 状态映射到步骤索引
    const statusStepMap: Record<string, number> = {
      'thinking': 0,
      'searching': 1, 
      'verifying': 2,
      'typing': 3,
      'idle': 4,
    }
    const stepIndex = statusStepMap[status] ?? 0
    const progress = status === 'idle' ? 0 : ((stepIndex + 1) / 4) * 100
    
    set({ 
      thinkingStatus: status, 
      thinkingStep: step || '',
      thinkingProgress: progress,
      thinkingStepIndex: stepIndex,
    })
  },
  
  setThinkingProgress: (progress, stepIndex?, totalSteps?) => set({
    thinkingProgress: progress,
    thinkingStepIndex: stepIndex ?? get().thinkingStepIndex,
    thinkingTotalSteps: totalSteps ?? get().thinkingTotalSteps,
  }),
  
  addPendingTimeout: (messageId, timeoutId) => {
    set((state) => {
      const newTimeouts = new Map(state.pendingTimeouts)
      // 清除旧的超时
      if (newTimeouts.has(messageId)) {
        clearTimeout(newTimeouts.get(messageId))
      }
      newTimeouts.set(messageId, timeoutId)
      return { pendingTimeouts: newTimeouts }
    })
  },
  
  clearPendingTimeout: (messageId) => {
    set((state) => {
      const newTimeouts = new Map(state.pendingTimeouts)
      if (newTimeouts.has(messageId)) {
        clearTimeout(newTimeouts.get(messageId))
        newTimeouts.delete(messageId)
      }
      return { pendingTimeouts: newTimeouts }
    })
  },
  
  markMessageAsFailed: (messageId) => {
    set((state) => {
      // 【bug修复】跨全部会话查找：失败消息可能不属于当前显示的会话
      // （旧版只查 currentSessionId，其他会话里失败的消息无法更新状态）
      const newMap = new Map(state.messagesBySession)
      newMap.forEach((msgs, sid) => {
        if (msgs.some((m) => m.id === messageId)) {
          newMap.set(sid, msgs.map((m) => (m.id === messageId ? { ...m, status: 'failed' as const } : m)))
        }
      })

      const failedMessageIds = state.failedMessageIds.includes(messageId)
        ? state.failedMessageIds
        : [...state.failedMessageIds, messageId]

      return { messagesBySession: newMap, failedMessageIds }
    })
    debouncedSave(() => saveToStorage(get()))
  },

  markMessageAsRetrying: (messageId) => {
    set((state) => {
      // 【bug修复】跨全部会话查找：将消息状态从 failed 改回 sending
      const newMap = new Map(state.messagesBySession)
      newMap.forEach((msgs, sid) => {
        if (msgs.some((m) => m.id === messageId)) {
          newMap.set(sid, msgs.map((m) => (m.id === messageId ? { ...m, status: 'sending' as const } : m)))
        }
      })

      const failedMessageIds = state.failedMessageIds.filter((id) => id !== messageId)
      return { messagesBySession: newMap, failedMessageIds }
    })
    debouncedSave(() => saveToStorage(get()))
  },

  retryAllFailed: (sendFn) => {
    const state = get()
    const failedIds = state.failedMessageIds
    if (failedIds.length === 0) return

    // 【bug修复】失败消息可能存在于任何会话（断线恢复时是跨全会话登记的），
    // 旧版只在 currentSessionId 里找 → 重启后非当前会话的失败消息永远无法重试，
    // 且列表还被静默清空
    const retryTexts: string[] = []
    state.messagesBySession.forEach((messages) => {
      messages.forEach((message) => {
        if (failedIds.includes(message.id) && message.role === 'user') {
          retryTexts.push(message.text)
        }
      })
    })

    // 把待重试消息标记为 sending（跨所有会话）
    set((state) => {
      const newMap = new Map(state.messagesBySession)
      newMap.forEach((msgs, sid) => {
        newMap.set(
          sid,
          msgs.map((m) =>
            failedIds.includes(m.id) && m.role === 'user' ? { ...m, status: 'sending' as const } : m
          )
        )
      })
      return { messagesBySession: newMap }
    })

    // 重新发送
    retryTexts.forEach((text) => sendFn(text))

    // 清空失败列表
    set({ failedMessageIds: [] })
  },

  initSessionMessages: (sessionId, messages) => {
    set((state) => {
      const newMap = new Map(state.messagesBySession)
      if (!newMap.has(sessionId)) {
        newMap.set(sessionId, messages)
      }
      return { messagesBySession: newMap }
    })
    debouncedSave(() => saveToStorage(get()))
  },

  mergeMessages: (sessionId, messages) => {
    set((state) => {
      const newMap = new Map(state.messagesBySession)
      const currentMessages = newMap.get(sessionId) || []
      const existingIds = new Set(currentMessages.map((m) => m.id))
      const newMessages = messages.filter((m) => !existingIds.has(m.id))
      
      // 合并后按时间戳排序，确保消息顺序逻辑正确
      const merged = [...currentMessages, ...newMessages].sort((a, b) => {
        // 主排序：时间戳升序（旧到新）
        if (a.timestamp !== b.timestamp) return a.timestamp - b.timestamp
        // 次排序：用户消息在AI回复之前（同一秒内）
        if (a.role === 'user' && b.role === 'ai') return -1
        if (a.role === 'ai' && b.role === 'user') return 1
        return 0
      })
      
      newMap.set(sessionId, merged)
      return { messagesBySession: newMap }
    })
    debouncedSave(() => saveToStorage(get()))
  },

  markMessageAsDone: (sessionId, messageId) => {
    set((state) => {
      const newMap = new Map(state.messagesBySession)
      const currentMessages = newMap.get(sessionId) || []
      newMap.set(
        sessionId,
        currentMessages.map((m) => (m.id === messageId ? { ...m, status: 'done' as const } : m))
      )
      return { messagesBySession: newMap }
    })
    debouncedSave(() => saveToStorage(get()))
  },

  // 【流式回复】追加增量文本：无该消息则创建 streaming 态 AI 气泡，有则拼接
  appendStreamText: (sessionId, messageId, delta) => {
    set((state) => {
      const newMap = new Map(state.messagesBySession)
      const currentMessages = newMap.get(sessionId) || []
      const idx = currentMessages.findIndex((m) => m.id === messageId)
      if (idx === -1) {
        newMap.set(sessionId, [...currentMessages, {
          id: messageId,
          role: 'ai' as const,
          text: delta,
          status: 'streaming' as const,
          timestamp: Date.now(),
          msgType: 'normal' as const,
        }])
      } else {
        const target = currentMessages[idx]
        currentMessages[idx] = { ...target, text: target.text + delta, status: 'streaming' as const }
        newMap.set(sessionId, currentMessages)
      }
      return { messagesBySession: newMap }
    })
    debouncedSave(() => saveToStorage(get()))
  },

  markSessionAsRead: (sessionId) => {
    set((state) => {
      const newMap = new Map(state.messagesBySession)
      const currentMessages = newMap.get(sessionId) || []
      // 检查是否有未读消息，避免无意义的更新
      const hasUnread = currentMessages.some(m => m.role === 'ai' && m.status === 'done' && !m.read)
      if (!hasUnread) return state
      // 将所有已完成的 AI 消息标记为已读
      newMap.set(
        sessionId,
        currentMessages.map((m) =>
          (m.role === 'ai' && m.status === 'done' && !m.read) ? { ...m, read: true } : m
        )
      )
      return { messagesBySession: newMap }
    })
    debouncedSave(() => saveToStorage(get()))
  },

  loadFromStorage: () => {
    const data = loadFromStorage()
    set(data)
  },

  saveToStorage: () => {
    saveToStorage(get())
  },

  resetStore: () => {
    set({
      messagesBySession: new Map(),
      currentSessionId: 'default',
      connectionStatus: 'connecting',
      emotionalState: { valence: 0.3, arousal: 0.4, intimacy: 0.5, trust: 0.5, dominant_emotion: 'content', dominant_label: '满足', dominant_emoji: '😌' },
      growthData: { level: 1, exp: 0, expMax: 100, intimacy: 0, intimacyStage: '初识' },
      isTyping: false,
      thinkingStatus: 'idle',
      thinkingStep: '',
      thinkingProgress: 0,
      thinkingStepIndex: 0,
      pendingTimeouts: new Map(),
      failedMessageIds: [],
    })
    try {
      localStorage.removeItem(STORAGE_KEY)
    } catch {
      /* ignore */
    }
  },
}))
