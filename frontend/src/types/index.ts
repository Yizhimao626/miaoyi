export interface Message {
  id: string
  role: 'user' | 'ai'
  text: string
  // 【消息状态链】sending(发送中) → sent(已送达) → thinking(思考中) → streaming(生成中)
  // → done(完成) → voice_loading(语音生成中) → failed(失败)
  status: 'sending' | 'sent' | 'thinking' | 'streaming' | 'done' | 'voice_loading' | 'failed' | 'typing'
  timestamp: number
  voiceText?: string
  audioUrl?: string
  voiceDuration?: number
  smallThought?: string
  thought?: string
  msgType?: 'normal' | 'thinking' | 'proactive' | 'thought_bubble' | 'voice' | 'image' | 'file'
  imageUrl?: string
  fileName?: string
  fileSize?: number
  fileType?: string
  fileUrl?: string
  replyTo?: string
  canContinue?: boolean
  finishReason?: string
  branchIndex?: number
  totalBranches?: number
  _hasRendered?: boolean
  // 消息已读状态 - 用于会话列表未读数统计
  read?: boolean
}

export interface Session {
  id: string
  name: string
  lastMessage?: string
  lastTime?: number
  unreadCount?: number
  avatar?: string
}

export interface Memory {
  id: string
  content: string
  timestamp: number
  pinned?: boolean
}

export interface GrowthData {
  level: number
  exp: number
  expMax: number
  intimacy: number
  intimacyStage: string
}

export interface EmotionalState {
  // 4 核心维度
  valence: number
  arousal: number
  intimacy: number
  trust: number
  // 后端实际返回的具体情感值（0-100）
  happy?: number
  sad?: number
  angry?: number
  love?: number
  excitement?: number
  calm?: number
  // 展示用字段
  dominant_emotion?: string
  dominant_label?: string
  dominant_emoji?: string
  emoji?: string
  description?: string
  dominant?: string
}

export interface UserProfile {
  id: string
  username: string
  phone?: string
  email?: string
  nickname?: string
  role?: string
  created_at?: number
  last_login?: number
}

export interface WebSocketMessage {
  type: string
  data?: any
}

export interface ThemeConfig {
  mode: 'light' | 'dark' | 'system'
}

export interface AppState {
  isLoggedIn: boolean
  isLoading: boolean
  showSplash: boolean
  showPrivacyModal: boolean
  theme: ThemeConfig
  mute: boolean
  networkStatus: 'online' | 'offline'
}