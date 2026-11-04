// -*- coding: utf-8 -*-
/**
 * 喵艺快速消息检测 Hook
 * 
 * 检测用户是否在短时间内发送了大量消息，并显示调皮提示
 * 
 * 触发条件：10 秒内发送 3 条以上消息
 * 
 * 效果：显示浮动提示，如：
 * - "你打字好快，本喵都来不及舔毛了喵～"
 * - "慢点慢点，尾巴都跟不上了~"
 */
import { useState, useRef, useCallback, useEffect } from 'react'

const FAST_MESSAGES = [
  '你打字好快，本喵都来不及舔毛了喵～',
  '慢点慢点，尾巴都跟不上了~',
  '喵～本喵正在努力跟上你的节奏！',
  '哇，你好活跃呀，本喵喜欢~',
  '呼～让本喵喘口气，尾巴快转不过来了~',
]

interface UseFastMessageDetector {
  /** 记录发送消息时间并检查是否需要显示提示 */
  recordSend: () => void
  /** 当前显示的提示文本 */
  fastMessageText: string
  /** 是否正在显示提示 */
  showIndicator: boolean
}

export function useFastMessageDetector(): UseFastMessageDetector {
  const sendTimestampsRef = useRef<number[]>([])
  const [showIndicator, setShowIndicator] = useState(false)
  const [fastMessageText, setFastMessageText] = useState('')
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // 清理定时器
  useEffect(() => {
    return () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current)
      }
    }
  }, [])

  const recordSend = useCallback(() => {
    const now = Date.now()
    
    // 清理 10 秒前的记录
    sendTimestampsRef.current = sendTimestampsRef.current.filter(
      t => now - t < 10000
    )
    sendTimestampsRef.current.push(now)

    // 如果 10 秒内发送了 3 条以上，显示调皮提示
    if (sendTimestampsRef.current.length >= 3) {
      const randomMsg = FAST_MESSAGES[
        Math.floor(Math.random() * FAST_MESSAGES.length)
      ]
      setFastMessageText(randomMsg)
      setShowIndicator(true)

      // 2 秒后自动隐藏
      if (timerRef.current) {
        clearTimeout(timerRef.current)
      }
      timerRef.current = setTimeout(() => {
        setShowIndicator(false)
      }, 2000)
    }
  }, [])

  return {
    recordSend,
    fastMessageText,
    showIndicator,
  }
}
