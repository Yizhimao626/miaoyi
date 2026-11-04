/**
 * 喵艺 · 通话状态管理（Zustand）
 * 管理：拨打 / 响铃 / 通话中 / 挂断 全流程状态 + 实时字幕 + 麦克风/扬声器
 *
 * 【ADAPT】已应用路径修正：项目实际是 src/store（无 s），原文档写的 src/stores 已纠正。
 * 【ADAPT】fetch 改用项目统一的 request() 封装，自动带 Bearer token；
 *          后端 enhance 路由从 token 解析 user_id，前端无需传 user_id。
 */
import { create } from 'zustand'
import { request } from '../utils/api'
import { useUIStore } from './uiStore'

export type CallStatus =
  | 'idle'          // 无通话
  | 'dialing'       // 拨号中（用户拨喵艺）
  | 'ringing'       // 响铃中（双向）
  | 'connected'     // 通话中
  | 'ended'         // 已结束

export type CallDirection = 'outgoing' | 'incoming'

export interface Subtitle {
  speaker: 'user' | 'bot'
  text: string
  ts: number
}

interface CallStore {
  status: CallStatus
  direction: CallDirection
  incomingReason: string
  duration: number
  subtitles: Subtitle[]
  micOn: boolean
  speakerOn: boolean
  endedReason: string
  _timer: number | null
  startOutgoing: () => void
  onIncoming: (reason: string) => void
  setStatus: (s: CallStatus) => void
  addSubtitle: (s: Subtitle) => void
  clearSubtitles: () => void
  tickDuration: () => void
  toggleMic: () => void
  toggleSpeaker: () => void
  endCall: (reason?: string) => void
  reset: () => void
}

export const useCallStore = create<CallStore>((set, get) => ({
  status: 'idle',
  direction: 'outgoing',
  incomingReason: '',
  duration: 0,
  subtitles: [],
  micOn: true,
  speakerOn: true,
  endedReason: '',
  _timer: null,

  startOutgoing: async () => {
    // 【通话无声修复】在用户点击手势内解锁 AudioContext，否则接通后 TTS 音频会被
    // 移动端自动播放策略拦截（表现为“接通了但没声音”）。
    ;(window as unknown as { __unlockCallAudio?: () => void }).__unlockCallAudio?.()
    // 【历史教训】旧版先 setStatus('dialing') 再发请求，请求失败也不回滚，
    // 导致 UI 永远卡在"正在拨打…"，用户觉得"打电话没反应"。
    // 修复：先 setStatus，请求失败时立刻回滚状态 + Toast 提示。
    set({ status: 'dialing', direction: 'outgoing', duration: 0, subtitles: [], endedReason: '' })
    try {
      const resp = await request('/call/dial', { method: 'POST', body: JSON.stringify({}) })
      if (resp && resp.code && resp.code !== 200 && resp.code !== 0) {
        // 后端返回业务错误（如 409 正在通话中、500 拨号失败）
        throw new Error(resp.message || '拨号失败')
      }
    } catch (e: any) {
      // 【修复】请求失败 → 回滚状态 + Toast 明确提示
      const msg = e?.message || String(e) || '网络错误，后端服务可能未启动'
      console.warn('[callStore] dial failed:', msg)
      set({ status: 'ended', endedReason: msg })
      useUIStore.getState().showToast(`📞 拨号失败：${msg}`, 'error')
      setTimeout(() => get().reset(), 2000)
    }
  },

  onIncoming: (reason: string) => {
    set({ status: 'ringing', direction: 'incoming', incomingReason: reason, duration: 0, subtitles: [] })
  },

  setStatus: (s) => set({ status: s }),
  addSubtitle: (s) => set((st) => ({ subtitles: [...st.subtitles, s].slice(-50) })),
  clearSubtitles: () => set({ subtitles: [] }),
  tickDuration: () => set((st) => ({ duration: st.duration + 1 })),
  toggleMic: () => set((st) => ({ micOn: !st.micOn })),
  toggleSpeaker: () => set((st) => ({ speakerOn: !st.speakerOn })),

  endCall: async (reason = '') => {
    const { status, direction } = get()
    try {
      if (status === 'connected' || status === 'dialing' || (status === 'ringing' && direction === 'outgoing')) {
        // 【bug修复】呼出响铃中挂断也必须通知后端：否则后端拨号流程成为孤儿，
        // 喵艺可能"接通"无人聆听的通话并突然播放问候语音，再次拨号还会撞上占线守卫
        await request('/call/hangup', { method: 'POST', body: JSON.stringify({}) }).catch(() => {})
      } else if (status === 'ringing' && direction === 'incoming') {
        await request('/call/decline', { method: 'POST', body: JSON.stringify({}) }).catch(() => {})
      }
    } catch {}
    set({ status: 'ended', endedReason: reason })
    setTimeout(() => get().reset(), 1800)
  },

  reset: () => set({ status: 'idle', duration: 0, subtitles: [], endedReason: '', incomingReason: '' }),
}))

export function answerIncoming() {
  request('/call/answer', { method: 'POST', body: JSON.stringify({}) })
    .then((resp) => {
      if (resp && resp.code && resp.code !== 200 && resp.code !== 0) {
        throw new Error(resp.message || '接听失败')
      }
      useUIStore.getState().showToast('📞 已接听通话', 'success')
    })
    .catch((e: any) => {
      const msg = e?.message || String(e) || '接听失败'
      useUIStore.getState().showToast(`📞 接听失败：${msg}`, 'error')
      console.warn('[callStore] answer failed:', msg)
    })
}
