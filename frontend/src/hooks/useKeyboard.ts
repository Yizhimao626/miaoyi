import { useState, useEffect, useCallback } from 'react'

interface UseKeyboardReturn {
  keyboardHeight: number
  isKeyboardOpen: boolean
  viewportHeight: number
}

/**
 * 键盘遮挡适配（Capacitor 8 / Android 15+ edge-to-edge）
 *
 * 唯一补偿量 = window.innerHeight - (visualViewport.height + offsetTop)
 * 即「布局视口底部未被视觉视口覆盖的高度」：
 * - 系统已把 WebView resize（adjustResize / Capacitor IME inset padding）：
 *   innerHeight 已收缩，差值恒为 0 → 不补偿，杜绝双重补偿；
 * - 键盘以 overlay 覆盖（旧 WebView、Android 14 机型 edge-to-edge）：
 *   差值 = 键盘高度 → 写入全局 --kb-inset，由 CSS 压缩 #root、抬升 fixed 底栏。
 */
export function useKeyboard(): UseKeyboardReturn {
  const [keyboardHeight, setKeyboardHeight] = useState(0)
  const [isKeyboardOpen, setIsKeyboardOpen] = useState(false)
  const [viewportHeight, setViewportHeight] = useState(window.innerHeight)

  const measure = useCallback(() => {
    const vv = window.visualViewport
    if (!vv) return
    const gap = Math.max(0, window.innerHeight - vv.height - vv.offsetTop)
    // 10px 阈值：滤掉状态栏/手势条等小 inset 抖动
    const kb = gap > 10 ? Math.round(gap) : 0

    setViewportHeight(vv.height)
    setKeyboardHeight(kb)
    setIsKeyboardOpen(kb > 0)
    document.documentElement.style.setProperty('--kb-inset', kb ? `${kb}px` : '0px')
    // 【输入框掉出屏幕修复】100dvh 在部分 Android WebView 里不随软键盘收缩，
    // 键盘弹出后 #root 底部（输入栏所在位置）仍在键盘下方。这里把视觉视口高度
    // 直接写成 CSS 变量，由 #root 使用，保证布局高度始终等于真实可见区域。
    document.documentElement.style.setProperty('--app-h', `${Math.round(vv.height)}px`)
    // 供 CSS 做键盘态条件样式（隐藏底部 tab 栏、收紧输入区留白）
    document.documentElement.setAttribute('data-kb', kb > 0 ? '1' : '0')
  }, [])

  useEffect(() => {
    const vv = window.visualViewport
    if (vv) {
      vv.addEventListener('resize', measure)
      vv.addEventListener('scroll', measure)
    }
    window.addEventListener('resize', measure)
    window.addEventListener('orientationchange', measure)
    // 聚焦时键盘可能尚未完成动画，立即测一次 + 延迟补测
    const onFocusIn = () => {
      measure()
      setTimeout(measure, 150)
      setTimeout(measure, 400)
    }
    document.addEventListener('focusin', onFocusIn)

    measure()

    return () => {
      if (vv) {
        vv.removeEventListener('resize', measure)
        vv.removeEventListener('scroll', measure)
      }
      window.removeEventListener('resize', measure)
      window.removeEventListener('orientationchange', measure)
      document.removeEventListener('focusin', onFocusIn)
      document.documentElement.style.setProperty('--kb-inset', '0px')
      document.documentElement.style.setProperty('--app-h', '')
      document.documentElement.setAttribute('data-kb', '0')
    }
  }, [measure])

  return {
    keyboardHeight,
    isKeyboardOpen,
    viewportHeight,
  }
}
