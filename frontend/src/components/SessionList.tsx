import { useState, useEffect, useRef, useMemo } from 'react'
import type { Session, Message } from '../types'
import { getSessionList, deleteSession, createSession, clearAllConversations } from '../utils/api'
import { dbManager } from '../utils/db'
import { parseSticker, extractStickers } from '../utils/sticker'
import { Icon } from './icons'

interface SessionListProps {
  // 面板是否展开：每次打开都重新从服务端同步（标题/最新消息可能已变化）
  isOpen?: boolean
  currentSessionId?: string
  onSelectSession: (session: Session) => void
  onShowToast: (message: string, type?: 'success' | 'error' | 'warning') => void
  // 可选：传入各会话的消息Map用于实时预览
  messagesBySession?: Map<string, Message[]>
  // 【修复 2026-10-01】关闭抽屉：原先只能点遮罩关闭，真机上遮罩被内容挡住时抽屉无法收回
  onClose?: () => void
  // 【#12 修复】会话被删除（或全部清空）后通知父组件兜底：
  // 若删除的是当前会话，App 需切换到剩余会话或清空，否则页面空白、消息发不进去
  onSessionDeleted?: (deletedId: string, remaining: Session[]) => void
}

/**
 * 获取消息预览文本 - 根据消息类型返回合适的预览
 */
function getMessagePreview(message?: Message): string {
  if (!message) return ''
  
  // 根据消息类型返回不同的预览
  switch (message.msgType) {
    case 'voice':
      return '语音消息'
    case 'image':
      return '图片'
    case 'file':
      return message.fileName || '文件'
    case 'thinking':
      return message.text ? message.text.slice(0, 30) : '正在思考...'
    default: {
      if (parseSticker(message.text)) return '[表情包]'
      const previewText = extractStickers(message.text || '').cleanText
      return previewText ? previewText.slice(0, 50) : ''
    }
  }
}

/**
 * 获取带前缀的预览 - 区分用户和AI消息
 */
function getPreviewWithSender(message?: Message): string {
  if (!message) return ''
  const preview = getMessagePreview(message)
  if (!preview) return ''
  const prefix = message.role === 'user' ? '我: ' : '喵艺: '
  return prefix + preview
}

export function SessionList({
  isOpen,
  currentSessionId,
  onSelectSession,
  onShowToast,
  messagesBySession,
  onClose,
  onSessionDeleted,
}: SessionListProps) {
  const [sessions, setSessions] = useState<Session[]>([])
  const [, setDeletingId] = useState<string | null>(null)
  const [dragStartX, setDragStartX] = useState<number>(0)
  const [activeDragId, setActiveDragId] = useState<string | null>(null)
  const [dragOffset, setDragOffset] = useState<number>(0)
  const itemRefs = useRef<Record<string, HTMLDivElement | null>>({})
  // 【对标微信 2026-10-01】会话搜索（本地过滤：名称+最后一条消息）
  const [searchQuery, setSearchQuery] = useState('')
  // 【对标微信 2026-10-01】置顶会话：持久化到 localStorage，排序时置顶在前
  const [pinnedIds, setPinnedIds] = useState<Set<string>>(() => {
    try { return new Set(JSON.parse(localStorage.getItem('miaoyi_pinned_sessions') || '[]')) } catch { return new Set() }
  })
  // 【对标微信 2026-10-01】左滑露出「置顶/删除」按钮组（旧版滑动过阈值直接删除，误触风险高）
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const SWIPE_REVEAL_W = 116 // 两个操作按钮宽度（56×2 + 边距）
  const SWIPE_OPEN_AT = -58

  // 启动时预取一次；之后每次面板打开都重新同步（标题/末条消息可能已在别处变化）
  useEffect(() => {
    loadSessions()
  }, [])

  useEffect(() => {
    if (isOpen) loadSessions()
  }, [isOpen])

  // 当 messagesBySession 变化时，更新会话列表的预览数据
  const enrichedSessions = useMemo(() => {
    if (!messagesBySession) return sessions
    
    return sessions.map(session => {
      const messages = messagesBySession.get(session.id)
      if (!messages || messages.length === 0) return session
      
      // 获取最后一条消息
      const lastMsg = messages[messages.length - 1]
      // 计算未读数 - 仅统计已完成且未读的 AI 消息
      const unreadCount = messages.filter(
        m => m.role === 'ai' && m.status === 'done' && !m.read
      ).length
      
      return {
        ...session,
        lastMessage: getPreviewWithSender(lastMsg),
        lastTime: lastMsg.timestamp,
        unreadCount: session.id === currentSessionId ? 0 : unreadCount,
      }
    })
  }, [sessions, messagesBySession, currentSessionId])

  // 【对标微信 2026-10-01】搜索过滤 + 置顶排序（置顶在前，组内保持原时间序）
  const visibleSessions = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    const filtered = q
      ? enrichedSessions.filter(s =>
          (s.name || '').toLowerCase().includes(q) ||
          (s.lastMessage || '').toLowerCase().includes(q))
      : enrichedSessions
    return [...filtered].sort((a, b) =>
      (pinnedIds.has(b.id) ? 1 : 0) - (pinnedIds.has(a.id) ? 1 : 0))
  }, [enrichedSessions, searchQuery, pinnedIds])

  const handleTogglePin = (id: string) => {
    setPinnedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) { next.delete(id); onShowToast('已取消置顶', 'success') }
      else { next.add(id); onShowToast('已置顶该会话', 'success') }
      try { localStorage.setItem('miaoyi_pinned_sessions', JSON.stringify([...next])) } catch { /* 存储满时忽略 */ }
      return next
    })
    setExpandedId(null)
  }

  const loadSessions = async () => {
    try {
      // 1. 本地缓存先秒显（弱网下立即有内容）
      const localSessions = await dbManager.getSessions()
      if (localSessions.length > 0) {
        setSessions(localSessions)
      }
      // 2. 以服务端为准全量同步：旧版只要本地有缓存就不再请求服务端，导致其他
      //    设备新建/删除的会话永远看不到，本地已删除的脏会话也一直残留
      const result = await getSessionList()
      const apiSessions: Session[] = result.data || []
      setSessions(apiSessions)
      const apiIds = new Set(apiSessions.map((s) => s.id))
      await Promise.all([
        ...apiSessions.map((s) => dbManager.saveSession(s)),
        ...localSessions
          .filter((s) => !apiIds.has(s.id))
          .map((s) => dbManager.deleteSession(s.id)),
      ])
    } catch {
      // 离线/服务端不可用：保留本地缓存展示，本地也空时才提示
      const localFallback = await dbManager.getSessions().catch(() => [])
      if (localFallback.length === 0) {
        onShowToast('加载会话失败', 'error')
      }
    }
  }

  const handleCreateSession = async () => {
    try {
      const result = await createSession('新会话')
      const newSession = result.data
      await dbManager.saveSession(newSession)
      setSessions(prev => [newSession, ...prev])
      onSelectSession(newSession)
      onShowToast('会话已创建', 'success')
    } catch {
      onShowToast('创建失败', 'error')
    }
  }

  const handleDelete = async (id: string) => {
    if (!confirm('确定要删除这个会话吗？')) return
    
    setDeletingId(id)
    try {
      await deleteSession(id)
      await dbManager.deleteSession(id)
      const next = sessions.filter(s => s.id !== id)
      setSessions(next)
      onShowToast('会话已删除', 'success')
      // 【#12 修复】通知父组件做兜底切换（删除当前会话时尤为关键）
      onSessionDeleted?.(id, next)
    } catch {
      onShowToast('删除失败', 'error')
    } finally {
      setDeletingId(null)
    }
  }

  const handleClearAll = async () => {
    if (!confirm('确定要清空所有会话吗？此操作不可恢复！')) return
    
    try {
      await clearAllConversations()
      await dbManager.clearAll()
      setSessions([])
      onShowToast('所有会话已清空', 'success')
      // 【#12 修复】空 id + 空列表表示全部清空，App 需清空当前会话
      onSessionDeleted?.('', [])
    } catch {
      onShowToast('清空失败', 'error')
    }
  }

  const formatTime = (timestamp?: number) => {
    if (!timestamp) return ''
    const date = new Date(timestamp)
    const now = new Date()
    const diff = now.getTime() - timestamp

    if (diff < 60000) return '刚刚'
    if (diff < 3600000) return `${Math.floor(diff / 60000)}分钟前`
    if (date.toDateString() === now.toDateString()) {
      return `${date.getHours()}:${date.getMinutes().toString().padStart(2, '0')}`
    }
    return `${date.getMonth() + 1}/${date.getDate()}`
  }

  // 【对标微信 2026-10-01】左滑露出操作按钮组，松手过半停在展开态，不再直接删除
  // 【bug 修复】记录触摸开始时是否已展开：展开态轻点应"仅收起不切换会话"，
  // 否则 touchend 先清掉 expandedId，随后的 click 会误触发会话切换。
  const wasExpandedRef = useRef(false)
  const handleTouchStart = (e: React.TouchEvent, id: string) => {
    wasExpandedRef.current = expandedId === id
    if (expandedId && expandedId !== id) setExpandedId(null)
    setDragStartX(e.touches[0].clientX)
    setActiveDragId(id)
    setDragOffset(0)
  }

  const handleTouchMove = (e: React.TouchEvent) => {
    if (!activeDragId) return
    const diff = e.touches[0].clientX - dragStartX
    setDragOffset(Math.max(-SWIPE_REVEAL_W, Math.min(0, diff)))
  }

  const handleTouchEnd = () => {
    if (activeDragId) {
      if (dragOffset < SWIPE_OPEN_AT) {
        setExpandedId(activeDragId)
      } else if (!wasExpandedRef.current) {
        setExpandedId(null)
      }
      // 展开态轻点：保持展开，由 click 的 handleSelect 收起且不切换会话
    }
    setActiveDragId(null)
    setDragOffset(0)
  }

  const handleSelect = (session: Session) => {
    // 本行已展开：仅收起，不切换会话（微信行为）
    if (expandedId === session.id) {
      setExpandedId(null)
      return
    }
    // 其他行展开：收起后正常切换（桌面端无 touchstart 预收起，需在此处理）
    if (expandedId) setExpandedId(null)
    onSelectSession(session)
  }

  return (
    // 【#11 修复】根类改为专属 session-list-panel：旧版复用 .sidebar，
    // <600px 时 fixed 定位被 overlay 的 transform 限制到屏外看不到列表，
    // ≥600px 时又被桌面媒体查询改成常驻布局。内部 header/tabs/content 等
    // sidebar-* 类都是独立类，不受影响
    <div className="session-list-panel">
      <div className="sidebar-header">
        <h2 className="sidebar-title"><Icon name="cat" size={18} />喵艺小屋</h2>
        {/* 【修复 2026-10-01】新增 × 关闭按钮（+ 仍为新建会话） */}
        <div className="session-header-actions">
          <button className="sidebar-close session-list-close btn-press" onClick={handleCreateSession} title="新建会话"><Icon name="edit" size={16} /></button>
          <button className="sidebar-close session-list-close btn-press" onClick={() => onClose?.()} title="收起"><Icon name="x" size={16} /></button>
        </div>
      </div>
      <div className="sidebar-tabs">
        <button className="sidebar-tab active btn-press">会话</button>
      </div>
      <div className="sidebar-content">
        {/* 【对标微信 2026-10-01】会话搜索框 */}
        <div className="session-search">
          <span className="session-search-icon"><Icon name="search" size={14} /></span>
          <input
            type="text"
            className="session-search-input"
            placeholder="搜索会话..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
          {searchQuery && (
            <button className="session-search-clear" onClick={() => setSearchQuery('')}><Icon name="x" size={12} /></button>
          )}
        </div>
        <div className="session-actions">
          <button className="settings-action-btn checkin btn-press" onClick={handleCreateSession}>
            <Icon name="edit" size={15} /> 新建会话
          </button>
          <button className="settings-action-btn btn-press" onClick={handleClearAll}>
            <Icon name="trash" size={15} /> 清空
          </button>
        </div>
        {visibleSessions.length === 0 ? (
          <div className="empty-text">{searchQuery ? '没有匹配的会话哦~' : '还没有会话哦~'}</div>
        ) : (
          <div className="session-list">
            {visibleSessions.map(session => {
              const isPinned = pinnedIds.has(session.id)
              const wrapperOffset = activeDragId === session.id
                ? dragOffset
                : expandedId === session.id ? -SWIPE_REVEAL_W + 4 : 0
              return (
              <div
                key={session.id}
                className={`session-wrapper ${expandedId === session.id ? 'expanded' : ''} ${activeDragId === session.id ? 'dragging' : ''}`}
              >
                <div
                  ref={el => { itemRefs.current[session.id] = el }}
                  className={`session-content-wrapper ${currentSessionId === session.id ? 'active' : ''} ${isPinned ? 'pinned' : ''}`}
                  onClick={() => handleSelect(session)}
                  onTouchStart={(e) => handleTouchStart(e, session.id)}
                  onTouchMove={handleTouchMove}
                  onTouchEnd={handleTouchEnd}
                  style={{
                    transform: `translateX(${wrapperOffset}px)`,
                    transition: activeDragId === session.id ? 'none' : 'transform 0.2s ease',
                  }}
                >
                  <div className="session-content">
                    <div className="session-avatar">
                      {session.avatar || <Icon name="cat" size={22} />}
                    </div>
                    <div className="session-info">
                      <div className={`session-name ${currentSessionId === session.id ? 'active' : ''}`}>
                        {isPinned && <span className="session-pin-flag"><Icon name="pin" size={12} /></span>}
                        {session.name}
                      </div>
                      <div className="session-preview">
                        {session.lastMessage || '暂无消息，开始聊天吧~'}
                      </div>
                    </div>
                    <div className="session-meta">
                      <div className="session-time">{formatTime(session.lastTime)}</div>
                      {session.unreadCount && session.unreadCount > 0 && (
                        <div className="session-unread">{session.unreadCount > 99 ? '99+' : session.unreadCount}</div>
                      )}
                    </div>
                  </div>
                </div>
                {/* 【对标微信 2026-10-01】左滑露出的操作按钮组：置顶 + 删除 */}
                <div className="session-action-btns">
                  <button
                    className={`session-swipe-btn pin ${isPinned ? 'unpin' : ''} btn-press`}
                    onClick={() => handleTogglePin(session.id)}
                  >
                    {isPinned ? '取消置顶' : '置顶'}
                  </button>
                  <button
                    className="session-swipe-btn delete btn-press"
                    onClick={() => {
                      setExpandedId(null)
                      handleDelete(session.id)
                    }}
                  >
                    删除
                  </button>
                </div>
              </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}