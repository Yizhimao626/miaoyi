import { useRef, useEffect, useCallback, useState, useMemo, useImperativeHandle, forwardRef } from 'react'
import type { ForwardedRef, ReactNode } from 'react'

/**
 * 喵艺聊天虚拟滚动（稳定重构版）
 *
 * 【历史教训】旧版有五重 Bug 导致闪屏/无文字/消息错位：
 *  1. 用数组 index 作 key → 消息增删时 React 按 index 复用 DOM，渲染出"其他聊天内容"
 *  2. contentVisibility:'auto' + IntersectionObserver 测高 → 反馈环，总高度跳变闪屏
 *  3. visibleRange 用固定 itemHeight 除法估算 → 变高消息范围错位，"没有文字"
 *  4. smooth 滚动与变高重算竞态 → 滚动定位漂移
 *  5. 每次滚动事件双 setState → 闪屏
 *
 * 修复：稳定 key + 前缀和二分 + ResizeObserver + rAF 节流 + forwardRef 命令式 API。
 */

export interface VirtualScrollHandle {
  scrollToBottom: () => void
  scrollToKey: (key: string | number) => void
  isNearBottom: () => boolean
}

interface VirtualScrollProps<T> {
  items: T[]
  itemHeight: number
  renderItem: (item: T, index: number, isVisible: boolean) => ReactNode
  onScroll?: (scrollTop: number) => void
  shouldAutoScroll?: boolean
  onNearBottom?: (isNearBottom: boolean) => void
  getKey?: (item: T, index: number) => string | number
  autoScrollThreshold?: number
}

const OVERSCAN = 4

function VirtualScrollInner<T>(
  {
    items,
    itemHeight,
    renderItem,
    onScroll,
    shouldAutoScroll = true,
    onNearBottom,
    getKey,
    autoScrollThreshold = 80,
  }: VirtualScrollProps<T>,
  ref: ForwardedRef<VirtualScrollHandle>
) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [visibleRange, setVisibleRange] = useState({ start: 0, end: 20 })
  // 测量高度（稳定 key -> height）
  const measuredHeightsRef = useRef<Map<string, number>>(new Map())
  // 布局版本号：测量变化时自增，触发 useMemo 重算前缀和
  const [layoutVersion, setLayoutVersion] = useState(0)
  // rAF 节流
  const rafScheduledRef = useRef(false)
  const latestScrollTopRef = useRef(0)
  // 前缀和的 ref 镜像，供回调读取最新值（避免闭包陈旧）
  const prefixSumsRef = useRef<number[]>([0])
  // 上一次 items 的 key 序列，用于检测尾部追加/会话切换
  const prevKeysRef = useRef<string[]>([])
  // 是否在底部（onNearBottom 回调去重）
  const nearBottomRef = useRef(true)
  const shouldAutoScrollRef = useRef(shouldAutoScroll)
  // 【#9 修复】测量变高后需要钉住底部（打字机逐字输出跟随），由 RO 回调设置、
  // layoutVersion 重算前缀和后的 effect 消费
  const pinBottomOnLayoutRef = useRef(false)

  useEffect(() => {
    shouldAutoScrollRef.current = shouldAutoScroll
  }, [shouldAutoScroll])

  const computeKey = useCallback(
    (item: T, index: number): string => {
      if (getKey) return String(getKey(item, index))
      return String(index)
    },
    [getKey]
  )

  // render 期计算 keys（避免 effect 时序导致的陈旧 key）
  const keys = useMemo(
    () => items.map((item, i) => computeKey(item, i)),
    [items, computeKey]
  )

  // render 期计算前缀和（keys/测量/版本变化时重算，保证首帧布局正确）
  const prefixSums = useMemo(() => {
    const measured = measuredHeightsRef.current
    const sums = new Array(keys.length + 1)
    sums[0] = 0
    for (let i = 0; i < keys.length; i++) {
      sums[i + 1] = sums[i] + (measured.get(keys[i]) ?? itemHeight)
    }
    return sums
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [keys, itemHeight, layoutVersion])
  prefixSumsRef.current = prefixSums

  // 二分：最大的 i 使 prefixSums[i] <= offset
  const findIndexAtOffset = useCallback((offset: number, sums: number[]): number => {
    if (sums.length <= 1) return 0
    let lo = 0
    let hi = sums.length - 1
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1
      if (sums[mid] <= offset) lo = mid
      else hi = mid - 1
    }
    return lo
  }, [])

  const computeVisibleRange = useCallback(
    (scrollTop: number, viewportH: number) => {
      const sums = prefixSumsRef.current
      if (sums.length <= 1) return { start: 0, end: 0 }
      let start = findIndexAtOffset(scrollTop, sums)
      start = Math.max(0, start - OVERSCAN)
      const target = scrollTop + viewportH
      // 最小的 i 使 prefixSums[i] >= target
      let lo = 0
      let hi = sums.length - 1
      while (lo < hi) {
        const mid = (lo + hi) >> 1
        if (sums[mid] >= target) hi = mid
        else lo = mid + 1
      }
      let end = Math.min(items.length, lo + OVERSCAN)
      if (end < start) end = start
      return { start, end }
    },
    [findIndexAtOffset, items.length]
  )

  const flush = useCallback(() => {
    rafScheduledRef.current = false
    const container = containerRef.current
    if (!container) return
    const scrollTop = latestScrollTopRef.current
    const range = computeVisibleRange(scrollTop, container.clientHeight)
    setVisibleRange((prev) => (prev.start === range.start && prev.end === range.end ? prev : range))
    onScroll?.(scrollTop)
    const totalHeight = prefixSumsRef.current[items.length] ?? 0
    const distance = totalHeight - scrollTop - container.clientHeight
    const nearBottom = distance <= autoScrollThreshold
    if (nearBottom !== nearBottomRef.current) {
      nearBottomRef.current = nearBottom
      onNearBottom?.(nearBottom)
    }
  }, [computeVisibleRange, onScroll, onNearBottom, items.length, autoScrollThreshold])

  // 滚动事件 → rAF 节流
  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const handleScroll = () => {
      latestScrollTopRef.current = container.scrollTop
      if (!rafScheduledRef.current) {
        rafScheduledRef.current = true
        requestAnimationFrame(flush)
      }
    }
    container.addEventListener('scroll', handleScroll, { passive: true })
    latestScrollTopRef.current = container.scrollTop
    flush()
    return () => container.removeEventListener('scroll', handleScroll)
  }, [flush])

  // ResizeObserver：测量可见 wrapper 高度（替代旧版 IntersectionObserver + content-visibility）
  useEffect(() => {
    const container = containerRef.current
    if (!container || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver((entries) => {
      let changed = false
      for (const entry of entries) {
        const key = (entry.target as HTMLElement).dataset.vkey
        if (!key) continue
        const h = entry.contentRect.height
        const old = measuredHeightsRef.current.get(key)
        if (old !== h && h > 0) {
          measuredHeightsRef.current.set(key, h)
          changed = true
        }
      }
      if (changed) {
        // 测量变化 → 重建前缀和（bump layoutVersion）
        // 【#9 修复】底部跟随模式下记录"钉底"意图：打字机逐字输出把气泡撑高时，
        // 浏览器默认保持 scrollTop 会让新文字进入屏幕外；前缀和重算后的 effect
        // 会把视口重新钉到新底部。用户手动上滚（nearBottom=false）后不跟随
        pinBottomOnLayoutRef.current = nearBottomRef.current
        setLayoutVersion((v) => v + 1)
      }
    })
    // 观察当前已挂载的 wrapper
    const elements = container.querySelectorAll<HTMLElement>('[data-vkey]')
    elements.forEach((el) => ro.observe(el))
    return () => ro.disconnect()
  }, [visibleRange, layoutVersion])

  // 【#9 修复】前缀和随 layoutVersion 重算后，消费 RO 记录的钉底意图，
  // 把 scrollTop 设为新总高，视口始终跟随变高内容的底部
  useEffect(() => {
    if (!pinBottomOnLayoutRef.current) return
    pinBottomOnLayoutRef.current = false
    const container = containerRef.current
    if (!container) return
    const newScrollTop = prefixSumsRef.current[keys.length] ?? container.scrollHeight
    container.scrollTop = newScrollTop
    latestScrollTopRef.current = newScrollTop
    flush()
  }, [layoutVersion, keys.length, flush])

  // items 变化：尾部追加 + shouldAutoScroll → 滚底；会话切换 → 回底；其他 → 锚点保持（浏览器默认）
  useEffect(() => {
    const container = containerRef.current
    if (!container || items.length === 0) return
    const newKeys = keys
    const oldKeys = prevKeysRef.current

    const isTailAppend =
      oldKeys.length > 0 &&
      newKeys.length > oldKeys.length &&
      oldKeys.every((k, i) => k === newKeys[i])

    const isFullReplace =
      oldKeys.length > 0 &&
      newKeys.length > 0 &&
      !oldKeys.some((k, i) => i < newKeys.length && k === newKeys[i])

    // 【#8 修复】首次挂载（oldKeys 为空）且 items 已有内容时，定位到最新消息：
    // 旧版无任何分支处理，视口显示列表最开头的历史消息，"回到底部"按钮还常亮
    const isFirstMount = oldKeys.length === 0

    if (isFirstMount) {
      if (shouldAutoScrollRef.current) {
        requestAnimationFrame(() => {
          // 此刻多数消息未测量、前缀和为估算值，测量收敛后钉底逻辑会逐帧校正
          container.scrollTop = prefixSumsRef.current[newKeys.length] ?? container.scrollHeight
          latestScrollTopRef.current = container.scrollTop
          flush()
        })
      }
    } else if (isFullReplace) {
      requestAnimationFrame(() => {
        container.scrollTop = shouldAutoScrollRef.current
          ? (prefixSumsRef.current[newKeys.length] ?? container.scrollHeight)
          : 0
        latestScrollTopRef.current = container.scrollTop
        flush()
      })
    } else if (isTailAppend && shouldAutoScrollRef.current) {
      // 用 auto 而非 smooth：smooth 与变高重算竞态是旧版闪屏主因之一
      requestAnimationFrame(() => {
        container.scrollTop = prefixSumsRef.current[newKeys.length] ?? container.scrollHeight
        latestScrollTopRef.current = container.scrollTop
        flush()
      })
    }
    // 非尾部追加且非全替换（中间增删）：依赖浏览器默认 scrollTop 保持
    prevKeysRef.current = newKeys
  }, [keys, items.length, flush])

  // 命令式 API
  useImperativeHandle(
    ref,
    (): VirtualScrollHandle => ({
      scrollToBottom: () => {
        const container = containerRef.current
        if (!container) return
        container.scrollTop = prefixSumsRef.current[items.length] ?? container.scrollHeight
        latestScrollTopRef.current = container.scrollTop
        nearBottomRef.current = true
        flush()
      },
      scrollToKey: (key) => {
        const container = containerRef.current
        if (!container) return
        const idx = keys.indexOf(String(key))
        if (idx === -1) return
        const offset = prefixSumsRef.current[idx] ?? 0
        container.scrollTop = offset
        latestScrollTopRef.current = offset
        flush()
      },
      isNearBottom: () => nearBottomRef.current,
    }),
    [items.length, keys, flush]
  )

  const totalHeight = prefixSums[items.length] ?? 0
  const startOffset = prefixSums[visibleRange.start] ?? 0
  const visibleItems = useMemo(
    () => items.slice(visibleRange.start, visibleRange.end),
    [items, visibleRange]
  )

  return (
    <div
      ref={containerRef}
      className="virtual-scroll-container"
      style={{
        overflowY: 'auto',
        position: 'relative',
        height: '100%',
        overscrollBehavior: 'contain',
        WebkitOverflowScrolling: 'touch',
      }}
    >
      <div style={{ height: totalHeight }} />
      <div
        style={{
          position: 'absolute',
          top: startOffset,
          left: 0,
          right: 0,
        }}
      >
        {visibleItems.map((item, idx) => {
          const globalIndex = visibleRange.start + idx
          const key = keys[globalIndex] ?? String(globalIndex)
          return (
            <div key={key} data-vkey={key} data-index={globalIndex} style={{ position: 'relative' }}>
              {renderItem(item, globalIndex, true)}
            </div>
          )
        })}
      </div>
    </div>
  )
}

export const VirtualScroll = forwardRef(VirtualScrollInner) as <T>(
  props: VirtualScrollProps<T> & { ref?: React.Ref<VirtualScrollHandle> }
) => ReactNode
