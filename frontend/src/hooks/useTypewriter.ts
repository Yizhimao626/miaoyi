import { useState, useRef, useEffect, useCallback } from 'react'

interface UseTypewriterOptions {
  speed?: number
  onComplete?: () => void
  onProgress?: (text: string) => void
}

interface UseTypewriterReturn {
  displayText: string
  isTyping: boolean
  isPaused: boolean
  startTyping: (text: string) => void
  appendText: (text: string) => void
  stopTyping: () => void
  skipTyping: () => void
  pauseTyping: () => void
  resumeTyping: () => void
}

export function useTypewriter(options: UseTypewriterOptions = {}): UseTypewriterReturn {
  const { speed = 30, onComplete, onProgress } = options
  
  const [displayText, setDisplayText] = useState('')
  const [isTyping, setIsTyping] = useState(false)
  const [isPaused, setIsPaused] = useState(false)
  
  const textRef = useRef('')
  const indexRef = useRef(0)
  const rafRef = useRef<number | null>(null)
  const lastUpdateRef = useRef(0)
  const batchSizeRef = useRef(0)
  const isPausedRef = useRef(false)
  const isCompletedRef = useRef(false)
  const onCompleteRef = useRef(onComplete)
  const onProgressRef = useRef(onProgress)
  const speedRef = useRef(speed)

  useEffect(() => {
    onCompleteRef.current = onComplete
    onProgressRef.current = onProgress
    speedRef.current = speed
  }, [onComplete, onProgress, speed])

  const cleanup = useCallback(() => {
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current)
      rafRef.current = null
    }
    textRef.current = ''
    indexRef.current = 0
    batchSizeRef.current = 0
    lastUpdateRef.current = 0
    isPausedRef.current = false
    isCompletedRef.current = false
  }, [])

  const batchUpdate = useCallback(() => {
    if (isPausedRef.current) {
      rafRef.current = requestAnimationFrame(batchUpdate)
      return
    }

    const now = performance.now()
    const interval = Math.max(16, speedRef.current)
    const elapsed = now - lastUpdateRef.current
    
    if (elapsed >= interval) {
      const remaining = textRef.current.length - indexRef.current
      if (remaining <= 0) {
        if (!isCompletedRef.current) {
          isCompletedRef.current = true
          setIsTyping(false)
          onCompleteRef.current?.()
          cleanup()
        }
        return
      }

      const batchSize = Math.max(1, batchSizeRef.current)
      const newIndex = Math.min(indexRef.current + batchSize, textRef.current.length)
      const newText = textRef.current.substring(0, newIndex)
      
      setDisplayText(newText)
      onProgressRef.current?.(newText)
      indexRef.current = newIndex
      lastUpdateRef.current = now
      
      const remainAfter = textRef.current.length - newIndex
      batchSizeRef.current = Math.min(10, Math.max(1, Math.floor(remainAfter / 15)))
    }
    
    rafRef.current = requestAnimationFrame(batchUpdate)
  }, [cleanup])

  const startTyping = useCallback((text: string) => {
    cleanup()
    
    textRef.current = text
    indexRef.current = 0
    lastUpdateRef.current = performance.now()
    batchSizeRef.current = Math.min(5, Math.max(1, Math.floor(text.length / 25)))
    isCompletedRef.current = false
    
    setDisplayText('')
    setIsTyping(true)
    setIsPaused(false)
    
    rafRef.current = requestAnimationFrame(batchUpdate)
  }, [batchUpdate, cleanup])

  const appendText = useCallback((text: string) => {
    const wasTyping = isTyping
    
    textRef.current += text
    
    if (!wasTyping) {
      indexRef.current = 0
      lastUpdateRef.current = performance.now()
      batchSizeRef.current = Math.min(5, Math.max(1, Math.floor(text.length / 25)))
      isCompletedRef.current = false
      
      setDisplayText('')
      setIsTyping(true)
      setIsPaused(false)
      
      rafRef.current = requestAnimationFrame(batchUpdate)
    }
  }, [batchUpdate, isTyping])

  const stopTyping = useCallback(() => {
    cleanup()
    setIsTyping(false)
    setIsPaused(false)
  }, [cleanup])

  const skipTyping = useCallback(() => {
    const fullText = textRef.current
    cleanup()
    setDisplayText(fullText)
    setIsTyping(false)
    onCompleteRef.current?.()
  }, [cleanup])

  const pauseTyping = useCallback(() => {
    isPausedRef.current = true
    setIsPaused(true)
  }, [])

  const resumeTyping = useCallback(() => {
    isPausedRef.current = false
    setIsPaused(false)
    lastUpdateRef.current = performance.now()
  }, [])

  useEffect(() => {
    return () => {
      cleanup()
    }
  }, [cleanup])

  return {
    displayText,
    isTyping,
    isPaused,
    startTyping,
    appendText,
    stopTyping,
    skipTyping,
    pauseTyping,
    resumeTyping,
  }
}