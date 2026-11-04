// -*- coding: utf-8 -*-
/**
 * 喵艺消息滑动手势 Hook
 *
 * 支持在消息气泡上左右滑动触发快捷操作：
 * - 左滑：显示删除按钮
 * - 右滑：触发回复
 *
 * 设计原则：
 * - 仅在移动端/触摸设备生效
 * - 滑动距离超过阈值才触发，避免误触
 * - 释放后自动回弹
 */
import { useState, useRef, useCallback, useEffect } from 'react'

export interface SwipeAction {
  type: 'reply' | 'delete' | null
  triggered: boolean
}

interface UseSwipeOptions {
  onSwipeLeft?: () => void  // 左滑回调（通常为删除）
  onSwipeRight?: () => void // 右滑回调（通常为回复）
  threshold?: number        // 触发阈值（px），默认 80
  maxOffset?: number        // 最大偏移（px），默认 120
}

interface SwipeState {
  offset: number
  isSwiping: boolean
  showAction: 'reply' | 'delete' | null
}

export function useMessageSwipe({
  onSwipeLeft,
  onSwipeRight,
  threshold = 80,
  maxOffset = 120,
}: UseSwipeOptions = {}) {
  const [state, setState] = useState<SwipeState>({
    offset: 0,
    isSwiping: false,
    showAction: null,
  })

  const startX = useRef(0)
  const startY = useRef(0)
  const currentOffset = useRef(0)
  const isHorizontal = useRef<boolean | null>(null)
  const touchActive = useRef(false)

  // 判断是否为触摸设备
  const isTouchDevice = useRef(false)
  useEffect(() => {
    isTouchDevice.current = 'ontouchstart' in window || navigator.maxTouchPoints > 0
  }, [])

  const handleTouchStart = useCallback((e: React.TouchEvent) => {
    if (!isTouchDevice.current) return
    if (e.touches.length !== 1) return

    startX.current = e.touches[0].clientX
    startY.current = e.touches[0].clientY
    currentOffset.current = 0
    isHorizontal.current = null
    touchActive.current = true
  }, [])

  const handleTouchMove = useCallback((e: React.TouchEvent) => {
    if (!touchActive.current || !isTouchDevice.current) return

    const deltaX = e.touches[0].clientX - startX.current
    const deltaY = e.touches[0].clientY - startY.current

    // 首次移动时判断方向（水平 or 垂直）
    if (isHorizontal.current === null) {
      const absX = Math.abs(deltaX)
      const absY = Math.abs(deltaY)
      // 垂直滑动占主导时，不处理（让滚动正常工作）
      if (absY > absX && absY > 10) {
        isHorizontal.current = false
        return
      }
      if (absX > 10) {
        isHorizontal.current = true
      }
    }

    if (!isHorizontal.current) return

    // 阻止垂直滚动
    if (e.cancelable) {
      e.preventDefault()
    }

    // 限制偏移范围
    let offset = deltaX
    if (offset > maxOffset) offset = maxOffset
    if (offset < -maxOffset) offset = -maxOffset

    currentOffset.current = offset
    setState({
      offset,
      isSwiping: true,
      showAction: offset > threshold ? 'reply' : offset < -threshold ? 'delete' : null,
    })
  }, [threshold, maxOffset])

  const handleTouchEnd = useCallback(() => {
    if (!touchActive.current) return
    touchActive.current = false

    const offset = currentOffset.current

    // 根据最终偏移触发对应操作
    if (offset > threshold && onSwipeRight) {
      onSwipeRight()
    } else if (offset < -threshold && onSwipeLeft) {
      onSwipeLeft()
    }

    // 回弹到原位
    setState({
      offset: 0,
      isSwiping: false,
      showAction: null,
    })
    currentOffset.current = 0
    isHorizontal.current = null
  }, [threshold, onSwipeLeft, onSwipeRight])

  // 鼠标事件支持（桌面端调试用）
  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    if (isTouchDevice.current) return
    startX.current = e.clientX
    startY.current = e.clientY
    currentOffset.current = 0
    isHorizontal.current = null
    touchActive.current = true
  }, [])

  useEffect(() => {
    if (isTouchDevice.current) return

    const handleMouseMove = (e: MouseEvent) => {
      if (!touchActive.current) return
      const deltaX = e.clientX - startX.current
      let offset = deltaX
      if (offset > maxOffset) offset = maxOffset
      if (offset < -maxOffset) offset = -maxOffset
      currentOffset.current = offset
      setState({
        offset,
        isSwiping: true,
        showAction: offset > threshold ? 'reply' : offset < -threshold ? 'delete' : null,
      })
    }

    const handleMouseUp = () => {
      if (!touchActive.current) return
      touchActive.current = false
      const offset = currentOffset.current
      if (offset > threshold && onSwipeRight) {
        onSwipeRight()
      } else if (offset < -threshold && onSwipeLeft) {
        onSwipeLeft()
      }
      setState({ offset: 0, isSwiping: false, showAction: null })
      currentOffset.current = 0
    }

    window.addEventListener('mousemove', handleMouseMove)
    window.addEventListener('mouseup', handleMouseUp)
    return () => {
      window.removeEventListener('mousemove', handleMouseMove)
      window.removeEventListener('mouseup', handleMouseUp)
    }
  }, [threshold, maxOffset, onSwipeLeft, onSwipeRight])

  return {
    offset: state.offset,
    isSwiping: state.isSwiping,
    showAction: state.showAction,
    touchHandlers: {
      onTouchStart: handleTouchStart,
      onTouchMove: handleTouchMove,
      onTouchEnd: handleTouchEnd,
      onMouseDown: handleMouseDown,
    },
  }
}
