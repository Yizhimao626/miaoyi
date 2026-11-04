/**
 * 喵艺 · 统一 TTS 服务（第四步：TTS 文字转语音系统）
 * ------------------------------------------------------------
 * 任何文字都通过同一个入口进行语音转换与播放：
 *   textToSpeech(text) → 文本预处理 → 后端合成 → 音频播放
 *
 * 职责：
 * - 文本预处理（去 emoji/markdown，保留中文/数字/英文/标点）
 * - 调用后端合成（synthesizeVoice）
 * - 音频 URL 拼接（APK 相对路径 → 绝对）
 * - 播放 + 停止 + 错误捕获
 * - ObjectURL 释放（防内存泄漏）
 * - autoplay 被拒时返回明确错误
 *
 * TTS 绝不阻塞聊天：失败只记录，不影响消息显示。
 */
import { synthesizeVoice } from '../utils/api'
import { getBaseUrl } from '../utils/config'
import { getMute } from '../utils/storage'

/** 播放状态 */
export type TtsPlayState = 'idle' | 'loading' | 'playing' | 'error'

/** 单例音频（避免多个音频互相覆盖） */
let _audio: HTMLAudioElement | null = null
let _objectUrl: string | null = null

/** 当前状态回调（订阅用） */
type StateListener = (state: TtsPlayState, error?: string) => void
const _listeners = new Set<StateListener>()

function notify(state: TtsPlayState, error?: string): void {
  _listeners.forEach((fn) => fn(state, error))
}

export function onTtsState(fn: StateListener): () => void {
  _listeners.add(fn)
  return () => _listeners.delete(fn)
}

function releaseObjectUrl(): void {
  if (_objectUrl) {
    try { URL.revokeObjectURL(_objectUrl) } catch { /* ignore */ }
    _objectUrl = null
  }
}

function cleanupAudio(): void {
  if (_audio) {
    try {
      _audio.onended = null
      _audio.onerror = null
      _audio.pause()
    } catch { /* ignore */ }
    _audio = null
  }
  releaseObjectUrl()
}

/**
 * 文本预处理：删除 Emoji 与 Markdown 符号，保留中文/数字/英文/标点。
 * 【铁律】绝不删除中文，绝不只保留数字。
 */
export function cleanTextForSpeech(text: string): string {
  if (!text) return ''
  return text
    // 【方案一】官方贴纸标记不朗读
    .replace(/\[sticker:[a-z0-9_]+\]/gi, '')
    // Emoji 范围
    .replace(/[\u{1F300}-\u{1F9FF}]/gu, '')
    .replace(/[\u{2600}-\u{27BF}]/gu, '')
    .replace(/[\u{1F000}-\u{1F02F}]/gu, '')
    .replace(/[\u{1FA00}-\u{1FAFF}]/gu, '')
    // Markdown
    .replace(/\*{1,2}([^*]+)\*{1,2}/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\n+/g, '，')
    // 中文数字英文标点都保留，只清理多余空白
    .replace(/\s+/g, ' ')
    .trim()
}

/** 拼接后端音频绝对地址（APK 相对路径修复） */
export function resolveAudioUrl(url: string): string {
  if (!url) return ''
  if (/^(https?:|blob:|data:)/.test(url)) return url
  const base = getBaseUrl()
  if (base.startsWith('http')) {
    return `${base}${url.startsWith('/') ? '' : '/'}${url}`
  }
  return url.startsWith('/') ? url : `/${url}`
}

/** 停止当前播放 */
export function stopTts(): void {
  cleanupAudio()
  notify('idle')
}

/** 是否正在播放 */
export function isTtsPlaying(): boolean {
  return !!_audio && !_audio.paused
}

/**
 * 统一 TTS 入口：文本 → 合成 → 播放。
 * @returns true=播放成功开始，false=失败（已记录，不抛错阻塞调用方）
 */
export async function textToSpeech(text: string): Promise<boolean> {
  // 空文本不调用 TTS
  const clean = cleanTextForSpeech(text)
  if (!clean) {
    notify('error', '文本为空，不调用 TTS')
    return false
  }

  // 静音则不播放
  if (getMute()) {
    notify('idle')
    return false
  }

  // 停止旧播放（避免多音频叠加）
  cleanupAudio()
  notify('loading')

  try {
    // 1. 请求后端合成
    const result = await synthesizeVoice(clean)
    const audioUrl = result?.data?.audio_url
    if (!audioUrl) {
      notify('error', '后端未返回音频地址')
      console.error('[TTS] 后端未返回 audio_url, text=', clean.slice(0, 30))
      return false
    }

    // 2. 拼接绝对地址
    const fullUrl = resolveAudioUrl(audioUrl)

    // 3. 创建并播放
    const audio = new Audio(fullUrl)
    _audio = audio

    return await new Promise<boolean>((resolve) => {
      let settled = false
      const finish = (ok: boolean, err?: string): void => {
        if (settled) return
        settled = true
        if (ok) notify('playing')
        else notify('error', err)
        resolve(ok)
      }

      audio.onplay = () => finish(true)
      audio.onended = () => { notify('idle'); cleanupAudio() }
      audio.onpause = () => { if (audio.paused && audio.currentTime === 0) { /* user stop */ } }
      audio.onerror = () => {
        console.error('[TTS] 音频播放失败', fullUrl.slice(0, 80))
        finish(false, '音频播放失败')
        cleanupAudio()
      }

      // autoplay 可能被浏览器拦截 → play() 返回 rejected Promise
      audio.play().catch((e: unknown) => {
        console.error('[TTS] play() 被拒绝（可能 autoplay 限制）:', e)
        finish(false, '浏览器阻止了自动播放，请点击页面后再试')
        cleanupAudio()
      })
    })
  } catch (e) {
    // 合成请求失败：不抛错，不阻塞聊天
    console.error('[TTS] 合成请求失败:', e)
    notify('error', '语音合成失败')
    return false
  }
}

/** 停止并释放（供组件卸载时调用） */
export function disposeTts(): void {
  cleanupAudio()
  _listeners.clear()
}
