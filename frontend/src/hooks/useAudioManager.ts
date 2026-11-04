import { useEffect, useRef, useCallback, useState } from 'react'
import { getBaseUrl } from '../utils/config'
import { fetchVoiceSegment } from '../utils/api'

interface TtsSegmentTask {
  taskId: string
  total: number
}

interface UseAudioManagerReturn {
  playAudio: (url: string) => void
  playAudioSequence: (firstUrl: string, task: TtsSegmentTask) => void
  stopAudio: () => void
  isPlaying: boolean
}

// 【声音叠加修复】模块级共享音频元素（单例）。
// useAudioManager 是 hook，若每处调用各自 useRef 创建独立 Audio 实例，
// ChatView（AI 回复自动播报）与 MessageBubble（语音按钮）同时播放时声音会叠加。
// 改为共享同一 audioRef：任何一处播放都会停掉上一处，保证全局只有一路声音。
const sharedAudioRef: { current: HTMLAudioElement | null } = { current: null }

// 【首句优先】分段续播代次 token：任何新播放/停止都会 +1，
// 旧续播循环在 await 恢复后检测到失配即自动退出，防止旧任务"还魂"续播。
const seqTokenRef = { current: 0 }

// 当前 playOne 的 settle 句柄：stopAudio 打断 await playOne(...) 的挂起
let settleRef: ((ok: boolean) => void) | null = null

// 【回声抑制】全局标志：喵艺 TTS 播报期间置 true，ASR（useVoiceDialog）据此忽略识别结果，
// 防止喵艺自己的声音被麦克风识别成"用户输入"形成无限回声循环。
function setBotSpeaking(v: boolean): void {
  ;(window as unknown as { __botSpeaking?: boolean }).__botSpeaking = v
}

/** 拼接后端音频绝对地址（APK 相对路径修复） */
function resolveFullUrl(url: string): string {
  if (/^(https?:|blob:|data:)/.test(url)) return url
  const base = getBaseUrl()
  if (base.startsWith('http')) {
    return `${base}${url.startsWith('/') ? '' : '/'}${url}`
  }
  return url.startsWith('/') ? url : `/${url}`
}

export function useAudioManager(): UseAudioManagerReturn {
  const audioRef = sharedAudioRef
  const [isPlaying, setIsPlaying] = useState(false)
  const isUnlockedRef = useRef(false)

  const unlockAudio = useCallback(() => {
    if (isUnlockedRef.current) return

    const audio = new Audio()
    audio.muted = true
    audio.play().then(() => {
      isUnlockedRef.current = true
      audio.pause()
    }).catch(() => {
      console.warn('Audio unlock failed')
    })

    document.removeEventListener('click', unlockAudio)
    document.removeEventListener('touchstart', unlockAudio)
  }, [])

  useEffect(() => {
    document.addEventListener('click', unlockAudio)
    document.addEventListener('touchstart', unlockAudio)

    return () => {
      document.removeEventListener('click', unlockAudio)
      document.removeEventListener('touchstart', unlockAudio)
    }
  }, [unlockAudio])

  /** 播放单个音频并等它结束（onended/onerror/被 stopAudio 打断时 resolve） */
  const playOne = useCallback((url: string): Promise<boolean> => {
    return new Promise<boolean>((resolve) => {
      if (audioRef.current) {
        try { audioRef.current.pause() } catch { /* ignore */ }
      }

      const fullUrl = resolveFullUrl(url)
      const audio = new Audio(fullUrl)
      audioRef.current = audio
      setIsPlaying(true)
      // 【回声抑制】播报开始，标记 __botSpeaking=true，让 ASR 忽略麦克风收到的喵艺声音
      setBotSpeaking(true)

      let done = false
      const settle = (ok: boolean): void => {
        if (done) return
        done = true
        settleRef = null
        setIsPlaying(false)
        setBotSpeaking(false)
        resolve(ok)
      }

      audio.onended = () => settle(true)
      audio.onerror = () => {
        console.warn('Audio play failed')
        settle(false)
      }
      settleRef = settle

      audio.play().catch((e) => {
        console.warn('Audio play rejected:', e)
        settle(false)
      })
    })
  }, [audioRef])

  /** 立即打断当前播放与续播循环 */
  const interrupt = useCallback((): void => {
    seqTokenRef.current++
    if (settleRef) {
      const s = settleRef
      settleRef = null
      s(false)
    }
  }, [])

  const playAudio = useCallback((url: string): void => {
    interrupt()
    void playOne(url)
  }, [interrupt, playOne])

  /**
   * 【首句优先】播放第一段，播完后从后端逐段拉取剩余分段续播。
   * 任一段拉取失败/超时（audio_url 为 null）即停止续播；
   * 期间任何 playAudio/stopAudio 都会通过 seqToken 打断本循环。
   */
  const playAudioSequence = useCallback((firstUrl: string, task: TtsSegmentTask): void => {
    const myToken = ++seqTokenRef.current
    void (async () => {
      const total = Math.max(1, task.total)
      const cached: (string | undefined)[] = [firstUrl]
      for (let i = 0; i < total; i++) {
        if (myToken !== seqTokenRef.current) return
        let url = cached[i]
        if (!url && i > 0) {
          try {
            const r = await fetchVoiceSegment(task.taskId, i)
            url = r?.data?.audio_url || undefined
          } catch {
            url = undefined
          }
          if (myToken !== seqTokenRef.current) return
          cached[i] = url
        }
        if (!url) return // 该段失败/超时/无更多 → 停止续播
        const ok = await playOne(url)
        if (!ok || myToken !== seqTokenRef.current) return
      }
    })()
  }, [playOne])

  const stopAudio = useCallback((): void => {
    interrupt()
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current = null
    }
    setIsPlaying(false)
    setBotSpeaking(false)
  }, [audioRef, interrupt])

  return {
    playAudio,
    playAudioSequence,
    stopAudio,
    isPlaying,
  }
}
