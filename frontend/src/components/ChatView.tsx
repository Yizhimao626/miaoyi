import { useState, useRef, useEffect, useCallback, useMemo, lazy, Suspense } from 'react'
import type { Message, EmotionalState } from '../types'
import { MessageBubble } from './MessageBubble'
import { VirtualScroll } from './VirtualScroll'
import type { VirtualScrollHandle } from './VirtualScroll'
// 【P2 懒加载】分享卡片按需分包
const ShareCard = lazy(() => import('./ShareCard').then((m) => ({ default: m.ShareCard })))
import { EmptyStateGuide } from './EmptyStateGuide'
import { groupMessages, formatDate, searchMessages as searchMessagesUtil } from '../utils/messageGroup'
import { getMute, setMute } from '../utils/storage'
import { deleteMessage, regenerateMessage } from '../utils/api'
import { useKeyboard } from '../hooks/useKeyboard'
import { useOnlineStatus, useHaptic } from '../hooks/useOnlineStatus'
import { useFastMessageDetector } from '../hooks/useFastMessageDetector'
import { useCallStore } from '../store/callStore'  // enhance 模块：拨号入口
import { InputBar } from './InputBar'
import { ThinkingIndicator } from './ThinkingIndicator'
import { Icon } from './icons'


interface ChatViewProps {
  messages: Message[]
  connectionStatus: 'connecting' | 'connected' | 'disconnected' | 'auth_failed'
  emotionalState: EmotionalState
  onSendMessage: (text: string, replyTo?: string, imageUrl?: string) => void
  onToggleSidebar: () => void
  onToggleTheme: () => void
  onShowToast: (message: string, type?: 'success' | 'error' | 'warning') => void
  onReconnect?: () => void
  onToggleSessionList?: () => void
  /** 返回首页（聊天页隐藏底部导航后，左上角作为返回入口） */
  onBackToHome?: () => void
  currentSessionName?: string
  /** 打开设置中的服务器地址弹窗（断连引导：APK 换网络后一键改地址） */
  onOpenServerSettings?: () => void
  onUpdateMessages?: (messages: Message[] | ((prev: Message[]) => Message[])) => void
  isAdmin?: boolean
  onOpenAdminPanel?: () => void
  isTyping?: boolean
  thinkingStatus?: string
  thinkingStep?: string
  thinkingProgress?: number
  thinkingStepIndex?: number
  thinkingTotalSteps?: number
  failedCount?: number
  onRetryAll?: () => void
  // 手动重试单条消息 - 重用原消息ID，避免创建重复消息
  onRetryMessage?: (messageId: string, text: string, replyTo?: string) => void
  // 标记当前会话为已读（用户滚动到底部或查看消息时调用）
  onMarkSessionRead?: () => void
  dailyFragment?: string | null
}

export function ChatView({
  messages,
  connectionStatus,
  emotionalState,
  onSendMessage,
  onToggleSidebar,
  onToggleTheme,
  onShowToast,
  onReconnect,
  onToggleSessionList,
  onBackToHome,
  currentSessionName = '喵艺',
  onUpdateMessages,
  isAdmin = false,
  onOpenAdminPanel,
  isTyping = false,
  thinkingStatus = 'idle',
  thinkingStep = '',
  thinkingProgress = 0,
  thinkingStepIndex = 0,
  thinkingTotalSteps = 4,
  failedCount = 0,
  onRetryAll,
  onRetryMessage,
  onMarkSessionRead,
  onOpenServerSettings,
  dailyFragment = null,
}: ChatViewProps) {
  const [isMuted, setIsMuted] = useState(getMute())
  // 【微信化】顶部功能收进"更多"弹出菜单，header 只保留菜单+名字+更多
  const [showHeaderMenu, setShowHeaderMenu] = useState(false)
  const [showNewMessageBtn, setShowNewMessageBtn] = useState(false)
  // 新消息计数 - 用户不在底部时累计，点击回到底部后清零
  const [newMessageCount, setNewMessageCount] = useState(0)
  const [contextMenu, setContextMenu] = useState<{
    message: Message
    x: number
    y: number
  } | null>(null)
  const [showShareCard, setShowShareCard] = useState(false)
  

  const [confirmDialog, setConfirmDialog] = useState<{
    message: Message
    type: 'delete'
  } | null>(null)
  const [replyingTo, setReplyingTo] = useState<Message | null>(null)
  const [scrollPaws, setScrollPaws] = useState<Array<{ id: number; x: number; y: number; side: 'left' | 'right' }>>([])
  const [isNearBottom, setIsNearBottom] = useState(true)
  
  // 消息搜索状态
  const [searchQuery, setSearchQuery] = useState('')
  const [isSearching, setIsSearching] = useState(false)
  const [searchResults, setSearchResults] = useState<Message[]>([])
  const [highlightedMessageId, setHighlightedMessageId] = useState<string | null>(null)

  const { keyboardHeight } = useKeyboard()
  
  // 网络状态检测 - 与 WebSocket 联动优化
  const isOnline = useOnlineStatus(connectionStatus === 'connected')
  const haptic = useHaptic()
  
  // 快速连续消息检测
  const { recordSend, fastMessageText, showIndicator: showFastMessageIndicator } = useFastMessageDetector()

  const messagesEndRef = useRef<HTMLDivElement>(null)
  const messagesContainerRef = useRef<HTMLDivElement>(null)
  const searchInputRef = useRef<HTMLInputElement>(null)
  // VirtualScroll 命令式 API 句柄：滚动到底部 / 滚动到指定 key（替代旧的 querySelector+scrollIntoView）
  const virtualScrollRef = useRef<VirtualScrollHandle>(null)
  // 【滚动竞态修复】用户发送消息后标记 true，AI 回复到达时强制滚到底部，
  // 不受 VirtualScroll onNearBottom 回调把 isNearBottom 设回 false 的竞态影响。
  // 用户主动上滑查看历史时清除（handleNearBottomChange），AI 回复到达则不强滚。
  const justSentRef = useRef(false)
  // 猫爪动画：跟踪上次滚动位置，计算滚动距离决定是否生成猫爪
  const lastScrollTopRef = useRef(0)
  const pawCounterRef = useRef(0)
  
  // 【审查修复】引用跳转高亮定时器：供 executeSearch 与 handleJumpToMessage 共用，
  // 新搜索前清掉遗留定时器，避免其到点误清搜索高亮
  const highlightTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // 根据搜索词过滤消息
  const filteredMessages = useMemo(() => {
    if (!searchQuery.trim()) {
      return messages
    }
    return searchMessagesUtil(messages, searchQuery)
  }, [messages, searchQuery])
  
  // 执行搜索
  const executeSearch = useCallback((query: string) => {
    // 新搜索前清掉引用跳转遗留的高亮定时器（见 highlightTimerRef 注释）
    if (highlightTimerRef.current) {
      clearTimeout(highlightTimerRef.current)
      highlightTimerRef.current = null
    }
    if (!query.trim()) {
      setSearchResults([])
      return
    }
    const results = searchMessagesUtil(messages, query)
    setSearchResults(results)

    if (results.length > 0) {
      // 高亮第一个匹配结果
      setHighlightedMessageId(results[0].id)
      // 通过 VirtualScroll 命令式 API 滚动到匹配消息（key 与 getKey 规则一致）
      // 旧版用 querySelector + scrollIntoView 会与虚拟滚动绝对定位冲突导致定位漂移
      setTimeout(() => {
        virtualScrollRef.current?.scrollToKey(`msg-${results[0].id}`)
      }, 100)
    }
  }, [messages])

  // 猫爪动画：由 VirtualScroll 的 onScroll 回调驱动
  // 【历史教训】旧版监听 messagesContainerRef 滚动事件，与虚拟滚动绝对定位竞态，
  // 且 .messages-container 与 VirtualScroll 双容器同时监听导致重复触发。现统一由
  // VirtualScroll 单一滚动源驱动，onScroll 直接传入 scrollTop。
  const pendingPawTimersRef = useRef<Set<ReturnType<typeof setTimeout>>>(new Set())
  const handleVirtualScroll = useCallback((scrollTop: number) => {
    const scrollDistance = Math.abs(scrollTop - lastScrollTopRef.current)
    if (scrollDistance > 100) {
      pawCounterRef.current++
      if (pawCounterRef.current % 3 === 0) {
        const side: 'left' | 'right' = Math.random() > 0.5 ? 'left' : 'right'
        const newPaw = {
          id: Date.now(),
          x: side === 'left' ? 5 : 95,
          y: Math.random() * 80 + 10,
          side,
        }
        setScrollPaws(prev => [...prev.slice(-5), newPaw])
        const t = setTimeout(() => {
          setScrollPaws(prev => prev.filter(p => p.id !== newPaw.id))
          pendingPawTimersRef.current.delete(t)
        }, 1500)
        pendingPawTimersRef.current.add(t)
      }
      lastScrollTopRef.current = scrollTop
    }
  }, [])

  // 组件卸载时清理所有待执行的猫爪定时器，避免卸载后 setState 警告
  useEffect(() => {
    const timers = pendingPawTimersRef.current
    return () => {
      timers.forEach(clearTimeout)
      timers.clear()
    }
  }, [])

  const scrollToBottom = useCallback(() => {
    // 使用 VirtualScroll 命令式 API（瞬时滚动，非 smooth）
    // 【历史教训】旧版用 container.scrollTo({behavior:'smooth'})，smooth 与变高重算竞态
    // 是闪屏/定位漂移主因之一。VirtualScroll 内部已用 'auto' 行为规避此问题。
    virtualScrollRef.current?.scrollToBottom()
    setShowNewMessageBtn(false)
    setNewMessageCount(0)
  }, [])

  // 新消息到达：VirtualScroll 内部已通过 shouldAutoScroll 处理尾部追加自动滚底，
  // 此处负责"用户刚发送消息后 AI 回复到达时强制滚底"（覆盖 isNearBottom 竞态）
  // 以及"用户不在底部时累计新消息计数"逻辑。
  useEffect(() => {
    if (messages.length === 0) return
    if (justSentRef.current) {
      // 用户刚发送消息，无论 isNearBottom 状态如何都强制滚到底部等回复
      requestAnimationFrame(() => scrollToBottom())
      const lastMsg = messages[messages.length - 1]
      // AI 回复到达后清除标记（下次用户发消息再设）
      if (lastMsg && lastMsg.role === 'ai') {
        justSentRef.current = false
        setNewMessageCount(0)
      }
    } else if (isNearBottom) {
      // 在底部时清零新消息计数
      setNewMessageCount(0)
    } else {
      // 用户不在底部，累计新消息计数（仅统计 AI 的新消息）
      const lastMsg = messages[messages.length - 1]
      if (lastMsg && lastMsg.role === 'ai') {
        setNewMessageCount(prev => prev + 1)
      }
    }
  }, [messages.length, isTyping, isNearBottom])

  // 键盘状态变化时自动滚动到底部
  useEffect(() => {
    if (keyboardHeight > 0) {
      // 键盘打开时，延迟一点时间等待布局完成再滚动
      const t = setTimeout(() => {
        scrollToBottom()
      }, 100)
      // 组件卸载或键盘高度再次变化前清除定时器，避免卸载后触发滚动
      return () => clearTimeout(t)
    }
  }, [keyboardHeight])

  const handleNearBottomChange = useCallback((nearBottom: boolean) => {
    setIsNearBottom(nearBottom)
    setShowNewMessageBtn(!nearBottom)
    // 用户主动上滑离开底部 → 清除 justSentRef，AI 回复到达时不强滚（符合微信行为）
    if (!nearBottom) {
      justSentRef.current = false
    }
    // 用户滚动到底部时，标记当前会话消息为已读
    if (nearBottom) {
      onMarkSessionRead?.()
      setNewMessageCount(0)
    }
  }, [onMarkSessionRead])

  // 输入区发送成功后的簿记（原 handleSend 尾部逻辑）：强制滚底 + 清新消息提示
  const handleInputSent = useCallback(() => {
    justSentRef.current = true
    setIsNearBottom(true)
    setShowNewMessageBtn(false)
    setNewMessageCount(0)
    // 消息异步入列 + 虚拟滚动重算，需多帧兜底确保滚到底
    requestAnimationFrame(() => scrollToBottom())
    setTimeout(() => scrollToBottom(), 120)
  }, [scrollToBottom])

  const handleToggleMute = () => {
    const newMute = !isMuted
    setIsMuted(newMute)
    setMute(newMute)
    onShowToast(newMute ? '已静音' : '已取消静音')
  }

  const handleCopyMessage = () => {
    if (contextMenu) {
      navigator.clipboard.writeText(contextMenu.message.text)
      onShowToast('已复制到剪贴板', 'success')
    }
    setContextMenu(null)
  }

  const handleDeleteMessage = async () => {
    if (!contextMenu) return
    setConfirmDialog({ message: contextMenu.message, type: 'delete' })
    setContextMenu(null)
  }

  const handleConfirmDelete = async () => {
    if (!confirmDialog) return
    
    const deletedId = confirmDialog.message.id
    try {
      // 【#4 修复】服务端历史消息 id 为纯数字，走 DELETE 接口；客户端临时消息
      // id 形如 msg_xxx（非数字），Number() 会得到 NaN 请求 /message/NaN 必然失败，
      // 这类消息直接本地移除
      if (/^\d+$/.test(String(deletedId))) {
        await deleteMessage(Number(deletedId))
      }
      // 【审查修复】被删消息若被其他消息引用，重建引用者对象强制 memo 更新，
      // 引用块随之显示"原消息已删除"，而非残留旧文本
      const newMessages = messages
        .filter(m => m.id !== deletedId)
        .map(m => m.replyTo === deletedId ? { ...m } : m)
      onUpdateMessages?.(newMessages)
      onShowToast('消息已删除', 'success')
    } catch (error) {
      onShowToast('删除失败', 'error')
    }
    setConfirmDialog(null)
  }

  const handleCancelDelete = () => {
    setConfirmDialog(null)
  }

  const handleRegenerate = async () => {
    if (!contextMenu) return
    
    try {
      const response = await regenerateMessage(contextMenu.message.text)
      const regeneratedText = response.data.text
      
      onUpdateMessages?.(prev => {
        const idx = prev.findIndex(m => m.id === contextMenu!.message.id)
        if (idx !== -1 && idx + 1 < prev.length && prev[idx + 1].role === 'ai') {
          const newMessages = [...prev]
          newMessages[idx + 1] = {
            ...newMessages[idx + 1],
            text: regeneratedText,
          }
          return newMessages
        }
        return prev
      })
      onShowToast('重新生成成功！', 'success')
    } catch (error) {
      onShowToast('重新生成失败', 'error')
    }
    setContextMenu(null)
  }

  // 【bug 修复 2026-10-01】支持直接传入消息：右滑回复调用 onReply(message)，
  // 旧版无视参数只读 contextMenu（右滑时为空）导致右滑回复永远无效。
  const handleReply = useCallback((message?: Message) => {
    const target = message ?? contextMenu?.message
    if (target) setReplyingTo(target)
    setContextMenu(null)
  }, [contextMenu])

  const handleContinue = useCallback((message: Message) => {
    const text = message.text || ''
    onSendMessage(`继续：${text}`)
  }, [onSendMessage])

  // 重试失败的消息 - 使用 retryMessage 重用原消息ID，避免创建重复消息
  const handleRetry = useCallback((message: Message) => {
    if (message.role !== 'user') return

    if (onRetryMessage) {
      // 使用新的 retryMessage 方法 - 重用原消息ID，状态由内部逻辑管理
      onRetryMessage(message.id, message.text, message.replyTo)
    } else {
      // 回退：旧逻辑（会创建新消息）
      onUpdateMessages?.((prev: Message[]) =>
        prev.map(m => m.id === message.id ? { ...m, status: 'sending' } : m)
      )
      onSendMessage(message.text)
    }
  }, [onRetryMessage, onUpdateMessages, onSendMessage])

  

  const handleReanswer = () => {
    if (!contextMenu) return
    
    const aiMessage = contextMenu.message
    const aiIndex = messages.findIndex(m => m.id === aiMessage.id)
    
    if (aiIndex > 0) {
      const userMessage = messages[aiIndex - 1]
      if (userMessage.role === 'user') {
        onSendMessage(userMessage.text)
      }
    }
    
    setContextMenu(null)
  }

  const handleContextMenu = useCallback((message: Message, x: number, y: number) => {
    // 【对标微信 2026-10-01】坐标钳制：滑动触发时传入 (0,0) 会导致菜单弹到屏幕左上角，
    // 长按在屏幕边缘时菜单也会被裁掉。按菜单实际尺寸（约 176×260）钳制到视口内。
    const MENU_W = 176
    const MENU_H = 264
    const clampedX = Math.max(8, Math.min(x || 8, window.innerWidth - MENU_W - 8))
    const clampedY = Math.max(8, Math.min(y || 8, window.innerHeight - MENU_H - 8))
    setContextMenu({ message, x: clampedX, y: clampedY })
  }, [])

  // 【对标微信 2026-10-01】点击引用块跳转到被引用的原消息并短暂高亮
  // 【性能】用 ref 转发 messages：回调引用恒定，不因流式更新打破 MessageBubble 的 memo
  // 【审查修复】ref 更新移入 useEffect，避免渲染期写 ref（并发渲染反模式）
  const messagesRef = useRef(messages)
  useEffect(() => {
    messagesRef.current = messages
  }, [messages])
  const getMessageById = useCallback(
    (id: string) => messagesRef.current.find(m => m.id === id),
    []
  )
  const handleJumpToMessage = useCallback((id: string) => {
    virtualScrollRef.current?.scrollToKey(`msg-${id}`)
    if (highlightTimerRef.current) clearTimeout(highlightTimerRef.current)
    setHighlightedMessageId(id)
    highlightTimerRef.current = setTimeout(() => setHighlightedMessageId(null), 2500)
  }, [])
  useEffect(() => () => { if (highlightTimerRef.current) clearTimeout(highlightTimerRef.current) }, [])

  const handleClickOutside = () => {
    setContextMenu(null)
  }

  const formatConnectionStatus = () => {
    switch (connectionStatus) {
      case 'connecting':
        return { text: '连接中…', color: '#FFB800', pulse: true }
      case 'connected':
        return { text: '在线', color: '#22C55E', pulse: false }
      case 'disconnected':
        return { text: '正在重连…', color: '#EF4444', pulse: true }
      case 'auth_failed':
        return { text: '登录失效', color: '#EF4444', pulse: false }
      default:
        return { text: '在线', color: '#22C55E', pulse: false }
    }
  }

  const connectionInfo = formatConnectionStatus()

  const groupedMessages = groupMessages(filteredMessages)
  const isAndroid = /Android/i.test(navigator.userAgent)

  return (
    <div className="chat-area">
      {/* 【第5步】右侧面板抽屉拉手：收起状态下轻拉打开喵艺小屋 */}
      <button className="sidebar-drawer-handle btn-press" onClick={onToggleSidebar} title="打开喵艺小屋">
        <span className="drawer-handle-icon"><Icon name="cat" size={16} /></span>
        <span className="drawer-handle-chevron"><Icon name="chevronLeft" size={14} /></span>
      </button>
      {/* 搜索栏 */}
      {isSearching && (
        <div className="search-bar">
          <div className="search-input-container">
            <span className="search-icon"><Icon name="search" size={16} /></span>
            <input
              ref={searchInputRef}
              type="text"
              className="search-input"
              placeholder="搜索消息..."
              value={searchQuery}
              onChange={(e) => {
                const value = e.target.value
                setSearchQuery(value)
                executeSearch(value)
              }}
              autoFocus
            />
            {searchQuery && (
              <button 
                className="search-clear-btn"
                onClick={() => {
                  setSearchQuery('')
                  setSearchResults([])
                  setHighlightedMessageId(null)
                }}
              >
                <Icon name="x" size={16} />
              </button>
            )}
          </div>
          <div className="search-actions">
            <span className="search-result-count">
              {searchResults.length > 0 
                ? `找到 ${searchResults.length} 条` 
                : searchQuery ? '无结果' : ''}
            </span>
            <button 
              className="search-close-btn"
              onClick={() => {
                setIsSearching(false)
                setSearchQuery('')
                setSearchResults([])
                setHighlightedMessageId(null)
              }}
            >
              完成
            </button>
          </div>
        </div>
      )}
      
      {/* 搜索按钮（在头部区域） */}
      {!isSearching && (
        <button 
          className="search-toggle-btn"
          onClick={() => {
            setIsSearching(true)
            setTimeout(() => searchInputRef.current?.focus(), 100)
          }}
        >
          <Icon name="search" size={16} />
        </button>
      )}
      
      {/* 离线状态横幅 */}
      {!isOnline && (
        <div className="offline-banner">
          <div className="offline-banner-content">
            <span className="offline-icon"><Icon name="wifiOff" size={18} /></span>
            <div className="offline-text">
              <div className="offline-title">网络被猫咬断了...喵～</div>
              <div className="offline-desc">检查下网络连接，本喵在等你哦</div>
            </div>
          </div>
        </div>
      )}
      
      {connectionStatus === 'disconnected' && isOnline && (
        <div className="connection-warning">
          <div className="connection-warning-content">
            <span className="connection-warning-icon"><Icon name="paw" size={18} /></span>
            <div className="connection-warning-text">
              <div className="connection-warning-title">喵～连接断了</div>
              {isAndroid ? (
                <div className="connection-warning-desc">
                  检查下：手机和电脑在同一WiFi，电脑防火墙允许访问哦~
                </div>
              ) : (
                <div className="connection-warning-desc">本喵正在努力重新连接中...</div>
              )}
            </div>
            {onReconnect && (
              <button 
                className="connection-retry-btn btn-press"
                onClick={() => {
                  haptic('medium')
                  onReconnect()
                  onShowToast('喵～正在重新连接中...', 'warning')
                }}
              >
                重连
              </button>
            )}
            {onOpenServerSettings && (
              <button 
                className="connection-server-btn btn-press"
                onClick={() => {
                  haptic('light')
                  onOpenServerSettings()
                }}
              >
                <Icon name="settings" size={16} /> 配置服务器
              </button>
            )}
          </div>
        </div>
      )}
      
      <header className="header">
        <button
          className="header-menu btn-press"
          onClick={onBackToHome ?? onToggleSidebar}
          aria-label={onBackToHome ? '返回首页' : '打开喵艺小屋'}
        >
          <Icon name={onBackToHome ? 'chevronLeft' : 'menu'} size={22} />
        </button>
        <div className="header-title-area">
          <h1 className="header-title">{currentSessionName}</h1>
          {/* 【生命感】喵艺 · 情绪 + 在线状态 */}
          <span className="header-subtitle">
            <span className="header-mood-badge">
              {emotionalState.dominant_emoji || '😊'} {emotionalState.dominant_label || '心情平静'}
            </span>
            <span className="header-status-dot" style={{ background: connectionInfo.color }} />
            <span className={`header-status-text ${connectionInfo.pulse ? 'pulse' : ''}`}>{connectionInfo.text}</span>
          </span>
        </div>
        <div className="header-right">
          <button
            className="header-more btn-press"
            onClick={() => setShowHeaderMenu((v) => !v)}
            title="更多"
            aria-label="更多功能"
          >
            <svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor" aria-hidden="true">
              <circle cx="5" cy="12" r="1.8" />
              <circle cx="12" cy="12" r="1.8" />
              <circle cx="19" cy="12" r="1.8" />
            </svg>
          </button>
          {/* 【微信化】菜单放 header 内（header 为 relative），right 锚定 header 右缘，
              宽屏右侧 dock 面板下也不会被裁切；旧位置在 header 外、offsetParent 是 app-container */}
          {showHeaderMenu && (
            <>
              <div className="panel-overlay" onClick={() => setShowHeaderMenu(false)} />
              <div className="header-more-menu">
                <div
                  className="header-more-item btn-press"
                  onClick={() => { setShowHeaderMenu(false); onToggleSidebar() }}
                >
                  <Icon name="cat" size={16} /> <span>喵艺小屋</span>
                </div>
                <div
                  className="header-more-item btn-press"
                  onClick={() => { setShowHeaderMenu(false); onToggleSessionList?.() }}
                >
                  <Icon name="list" size={16} /> <span>会话列表</span>
                </div>
                <div
                  className="header-more-item btn-press"
                  onClick={() => { setShowHeaderMenu(false); useCallStore.getState().startOutgoing() }}
                >
                  <Icon name="phone" size={16} /> <span>打电话给喵艺</span>
                </div>
                <div
                  className="header-more-item btn-press"
                  onClick={() => { setShowHeaderMenu(false); handleToggleMute() }}
                >
                  <Icon name={isMuted ? 'volumeOff' : 'volume'} size={16} /> <span>{isMuted ? '取消静音' : '静音'}</span>
                </div>
                <div
                  className="header-more-item btn-press"
                  onClick={() => { setShowHeaderMenu(false); onToggleTheme() }}
                >
                  <Icon name="palette" size={16} /> <span>切换主题</span>
                </div>
                {isAdmin && (
                  <div
                    className="header-more-item btn-press"
                    onClick={() => { setShowHeaderMenu(false); onOpenAdminPanel?.() }}
                  >
                    <Icon name="shield" size={16} /> <span>管理面板</span>
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </header>


      {/* 思考状态指示器 —— 独立组件，见 ThinkingIndicator.tsx */}
      <ThinkingIndicator
        status={thinkingStatus}
        step={thinkingStep}
        progress={thinkingProgress}
        stepIndex={thinkingStepIndex}
        totalSteps={thinkingTotalSteps}
      />
      
      {/* 批量重试提示 */}
      {failedCount > 0 && (
        <div className="failed-messages-banner">
          <span className="failed-icon"><Icon name="alert" size={16} /></span>
          <span className="failed-text">{failedCount} 条消息发送失败</span>
          <button className="retry-all-btn btn-press" onClick={onRetryAll}>
            全部重试
          </button>
          <button 
            className="dismiss-btn btn-press" 
            onClick={() => onUpdateMessages?.((prev: Message[]) => 
              prev.map(m => m.status === 'failed' ? { ...m, status: 'done' as const } : m)
            )}
          >
            忽略
          </button>
        </div>
      )}

      <div
        className="messages-container"
        ref={messagesContainerRef}
        style={{
          // 【历史教训】.messages-container 旧版有 overflow-y:auto，与 VirtualScroll 内部
          // overflowY:auto 形成双滚动容器：外层也会滚动，导致"上下滑动回到顶部/无法定位
          // 到最新消息"。此处强制 overflow:hidden，仅由 VirtualScroll 单一容器负责滚动。
          overflow: 'hidden',
          // padding 统一由 CSS 控制（12px）；键盘适配走全局 --kb-inset，
          // 旧的 paddingBottom:100+keyboardHeight 是 overlay 时代 hack，已删除。
        }}
      >
        {scrollPaws.map((paw) => (
          <span
            key={paw.id}
            className={`scroll-paw ${paw.side}`}
            style={{
              left: `${paw.x}%`,
              top: `${paw.y}%`,
            }}
          >
            <Icon name="paw" size={16} />
          </span>
        ))}
        {groupedMessages.length > 0 && (
        <VirtualScroll
          ref={virtualScrollRef}
          items={groupedMessages}
          itemHeight={80}
          // 稳定 key：消息用 msg-{id}，时间分隔用 time-{timestamp}
          // 【历史教训】旧版未传 getKey → VirtualScroll 回退用数组 index 作 key，
          // 消息增删时 React 按 index 复用 DOM，渲染出"其他聊天内容"。
          getKey={(g) => g.type === 'time' ? `time-${g.timestamp}` : `msg-${g.message!.id}`}
          onScroll={handleVirtualScroll}
          shouldAutoScroll={isNearBottom}
          onNearBottom={handleNearBottomChange}
          renderItem={(group, idx, isVisible) => (
            <>
              {group.type === 'time' && group.timestamp && (
                <div className="time-divider">
                  <div className="time-divider-line" />
                  <div className="time-divider-text">{formatDate(group.timestamp)}</div>
                  <div className="time-divider-line" />
                </div>
              )}
              {group.type === 'message' && group.message && (
                <div
                  data-message-id={group.message.id}
                  className={`message-wrapper ${highlightedMessageId === group.message.id ? 'highlighted' : ''}`}
                >
                  <MessageBubble
                    message={group.message}
                    onLongPress={handleContextMenu}
                    onReply={handleReply}
                    isVisible={isVisible}
                    onContinue={handleContinue}
                    emotionalState={emotionalState}
                    onRetry={handleRetry}
                    animationDelay={idx * 50}
                    isNew={isVisible && idx >= groupedMessages.length - 3}
                    searchQuery={searchQuery}
                    isHighlighted={highlightedMessageId === group.message.id}
                    getMessageById={getMessageById}
                    onJumpToMessage={handleJumpToMessage}
                    onPickOption={(text) => onSendMessage(text)}
                  />
                </div>
              )}
            </>
          )}
        />)}
        
        {/* 空状态引导 - 使用独立组件 */}
        {messages.length === 0 && (
          <EmptyStateGuide
            onSendMessage={onSendMessage}
            onHaptic={haptic}
          />
        )}
        
        {dailyFragment && messages.length === 0 && (
          <div className="daily-fragment">
            <div className="fragment-icon"><Icon name="book" size={18} /></div>
            <div className="fragment-content">
              <div className="fragment-title">今日猫生碎片</div>
              <div className="fragment-text">{dailyFragment}</div>
            </div>
          </div>
        )}
        
        <div ref={messagesEndRef} />
      </div>

      {/* 快速连续消息提示 */}
      {showFastMessageIndicator && (
        <div className="fast-message-indicator">
          <span><Icon name="paw" size={14} /> {fastMessageText}</span>
        </div>
      )}

      <button
        className={`new-message-btn btn-press ${showNewMessageBtn ? 'show' : ''} ${newMessageCount > 0 ? 'has-new' : ''}`}
        onClick={scrollToBottom}
        title="回到底部"
      >
        {/* 猫爪拍打动画图标 */}
        <span className="scroll-icon"><Icon name="paw" size={22} /></span>
        {/* 新消息计数徽章 */}
        {newMessageCount > 0 && (
          <span className="new-message-badge">
            {newMessageCount > 99 ? '99+' : newMessageCount}
          </span>
        )}
        {/* 新消息提示文案 */}
        {newMessageCount > 0 && (
          <span className="new-message-hint">有新消息喵～</span>
        )}
      </button>

      <button
        className="share-btn-float btn-press"
        onClick={() => setShowShareCard(true)}
        title="分享对话"
      >
        <Icon name="share" size={20} />
      </button>

      {/* 输入区（含表情/贴纸/语音面板）—— 独立组件，见 InputBar.tsx */}
      <InputBar
        onSendMessage={onSendMessage}
        onShowToast={onShowToast}
        onUpdateMessages={onUpdateMessages}
        recordSend={recordSend}
        replyingTo={replyingTo}
        onCancelReply={() => setReplyingTo(null)}
        onSent={handleInputSent}
      />
      {contextMenu && (
        <>
          <div className="sidebar-mask show" onClick={handleClickOutside} />
          <div
            className="context-menu"
            style={{ left: contextMenu.x, top: contextMenu.y }}
          >
            <div className="context-menu-item btn-press" onClick={handleCopyMessage}><Icon name="copy" size={16} /> 复制</div>
            <div className="context-menu-item btn-press" onClick={() => handleReply()}><Icon name="reply" size={16} /> 回复</div>
            {contextMenu.message.role === 'user' && (
              <>
                <div className="context-menu-divider" />
                <div className="context-menu-item danger btn-press" onClick={handleDeleteMessage}><Icon name="trash" size={16} /> 删除</div>
              </>
            )}
            {contextMenu.message.role === 'ai' && (
              <>
                <div className="context-menu-divider" />
                <div className="context-menu-item btn-press" onClick={handleReanswer}><Icon name="refresh" size={16} /> 让喵艺重新回答</div>
              </>
            )}
            <div className="context-menu-divider" />
            <div className="context-menu-item btn-press" onClick={handleRegenerate}><Icon name="refresh" size={16} /> 重新生成</div>
          </div>
        </>
      )}

      {confirmDialog && (
        // 【#7 修复】统一使用 overlay 遮罩 + 卡片模式（与 SettingsTab 一致）。
        // 点遮罩空白处取消；点卡片内部不冒泡
        <div className="confirm-dialog-overlay" onClick={handleCancelDelete}>
          <div className="confirm-dialog" onClick={(e) => e.stopPropagation()}>
            <div className="confirm-icon"><Icon name="alert" size={30} /></div>
            <div className="confirm-title">确认删除</div>
            <div className="confirm-message">确定要删除这条消息吗？此操作无法撤销。</div>
            <div className="confirm-actions">
              <button className="confirm-btn cancel btn-press" onClick={handleCancelDelete}>取消</button>
              <button className="confirm-btn confirm btn-press" onClick={handleConfirmDelete}>删除</button>
            </div>
          </div>
        </div>
      )}

      {showShareCard && (
        <Suspense fallback={null}>
          <ShareCard
            messages={messages}
            onClose={() => setShowShareCard(false)}
          />
        </Suspense>
      )}
    </div>
  )
}
