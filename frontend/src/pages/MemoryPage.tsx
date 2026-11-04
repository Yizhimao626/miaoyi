import { useState, useEffect, useCallback } from 'react'
import { getMemories, deleteMemory, clearMemories, getMemoryTags, getMemoriesByTag } from '../utils/api'
import { Icon } from '../components/icons'

interface MemoryItem {
  id: string
  content: string
  timestamp: number
  emotion?: string
  tags?: string[]
}

interface MemoryPageProps {
  onShowToast?: (message: string, type?: 'success' | 'error' | 'warning') => void
}

/* 六宫格分类：icon 用官方资产，count 为前端按关键词估算 */
const CATEGORIES = [
  { id: 'important', label: '重要记忆', icon: 'miaoyi/icons/icon_31.png', kw: /重要|一定|永远|约定|答应|生日|纪念|喜欢/ },
  { id: 'about', label: '关于我', icon: 'miaoyi/icons/icon_33.png', kw: /我是|我的|我叫|职业|工作|上学|生日|爱好|家里/ },
  { id: 'shared', label: '共同经历', icon: 'miaoyi/icons/icon_38.png', kw: /一起|我们|上次|那天|陪|去了|聊过/ },
  { id: 'emotion', label: '情感记忆', icon: 'miaoyi/icons/icon_32.png', kw: null },
  { id: 'diary', label: '喵艺日记', icon: 'miaoyi/icons/icon_35.png', kw: /日记|今天喵艺|记录/ },
  { id: 'all', label: '全部记忆', icon: 'miaoyi/icons/icon_34.png', kw: null },
]

export function MemoryPage({ onShowToast }: MemoryPageProps) {
  const [memories, setMemories] = useState<MemoryItem[]>([])
  const [tags, setTags] = useState<string[]>([])
  const [searchQuery, setSearchQuery] = useState('')
  const [activeCategory, setActiveCategory] = useState<string | null>(null)
  const [searchMode, setSearchMode] = useState(false)

  const loadMemories = useCallback(async (query?: string) => {
    try {
      // 大窗口拉取（后端上限 500）：保证六宫格分类计数不被默认 50 条截断
      const res: any = await (query?.trim() ? getMemoriesByTag(query.trim()) : getMemories(500))
      const list = (res?.data || [])
        .map((m: any) => {
          const id = String(m.id ?? m.key ?? '')
          // 后端 created_ts 可能是秒级浮点（如 1790749662.4），统一转毫秒
          let ts = Number(m.timestamp ?? m.created_ts ?? 0)
          if (ts > 0 && ts < 1e12) ts = Math.round(ts * 1000)
          return {
            id,
            content: String(m.content ?? m.value ?? '').trim(),
            timestamp: ts || Date.now(),
            emotion: m.emotion_type || '',
            tags: Array.isArray(m.tags) ? m.tags : [],
          }
        })
        // 【精细化】过滤噪音：空内容、把 ID 当内容、纯数字、短英文噪音词（hotpot 等）
        .filter((m: MemoryItem) => {
          const c = m.content
          if (!c || c.length < 2) return false
          if (c === m.id) return false
          if (/^[\d.\s]+$/.test(c)) return false
          if (/^(none|null|undefined|unknown|n\/a)$/i.test(c)) return false
          if (/^[a-z0-9_\-\s]{1,8}$/i.test(c) && !/[\u4e00-\u9fa5]/.test(c)) return false
          return true
        })
        // 时间线按时间倒序（新的在前）
        .sort((a: MemoryItem, b: MemoryItem) => b.timestamp - a.timestamp)
      setMemories(list)
    } catch { setMemories([]) }
  }, [])

  useEffect(() => { loadMemories() }, [loadMemories])

  useEffect(() => {
    getMemoryTags().then((res: any) => setTags(res?.data || [])).catch(() => {})
  }, [])

  const handleDelete = async (id: string) => {
    if (!confirm('确定删除这条记忆吗？')) return
    try {
      await deleteMemory(id)
      setMemories(prev => prev.filter(m => m.id !== id))
      onShowToast?.('记忆已删除', 'success')
    } catch {
      onShowToast?.('删除失败', 'error')
    }
  }

  const handleClear = async () => {
    if (!confirm('确定清除全部记忆吗？此操作不可恢复！')) return
    try {
      await clearMemories()
      setMemories([])
      setActiveCategory(null)
      setSearchMode(false)
      onShowToast?.('记忆已清空', 'success')
    } catch {
      onShowToast?.('清除失败', 'error')
    }
  }

  const handleSearch = () => {
    if (!searchQuery.trim()) return
    loadMemories(searchQuery)
    setSearchMode(true)
    setActiveCategory(null)
  }

  const backToAll = () => {
    setSearchMode(false)
    setActiveCategory(null)
    setSearchQuery('')
    loadMemories()
  }

  const formatTime = (ts: number) => {
    const d = new Date(ts)
    const now = new Date()
    const pad = (n: number) => String(n).padStart(2, '0')
    const sameDay = d.toDateString() === now.toDateString()
    if (sameDay) return `今天 ${pad(d.getHours())}:${pad(d.getMinutes())}`
    const yesterday = new Date(now); yesterday.setDate(now.getDate() - 1)
    if (d.toDateString() === yesterday.toDateString()) return `昨天 ${pad(d.getHours())}:${pad(d.getMinutes())}`
    if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}月${d.getDate()}日`
    return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`
  }

  const countByCategory = (catId: string) => {
    if (catId === 'all') return memories.length
    const cat = CATEGORIES.find(c => c.id === catId)
    if (!cat) return 0
    if (cat.id === 'emotion') return memories.filter(m => !!m.emotion).length
    return memories.filter(m => cat.kw?.test(m.content) || m.tags?.some(t => cat.kw?.test(t))).length
  }

  const visibleMemories = (() => {
    if (searchMode) return memories
    if (!activeCategory || activeCategory === 'all') return memories
    const cat = CATEGORIES.find(c => c.id === activeCategory)
    if (!cat) return memories
    if (cat.id === 'emotion') return memories.filter(m => !!m.emotion)
    return memories.filter(m => cat.kw?.test(m.content) || m.tags?.some(t => cat.kw?.test(t)))
  })()

  const pageTitle = searchMode
    ? `搜索“${searchQuery.trim()}”`
    : activeCategory
      ? (CATEGORIES.find(c => c.id === activeCategory)?.label || '记忆')
      : null

  /* 时间线渲染上限：计数用全量，渲染只取最近 100 条，避免长列表卡顿 */
  const DISPLAY_CAP = 100
  const displayedMemories = visibleMemories.slice(0, DISPLAY_CAP)

  return (
    <div className="memory-page miaoyi-scroll">
      <div className="memory-page-header">
        <h1 className="memory-page-title">
          <img src="miaoyi/icons/icon_30.png" alt="" className="memory-title-ico" />
          我的记忆
        </h1>
        <button className="memory-clear-btn btn-press" onClick={handleClear}>清空</button>
      </div>

      {/* 搜索 */}
      <div className="memory-search-bar">
        <span className="memory-search-icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
            <circle cx="11" cy="11" r="6.5" />
            <path d="m20 20-3.8-3.8" />
          </svg>
        </span>
        <input
          type="text"
          className="memory-search-input"
          placeholder="搜索记忆..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
        />
        <button className="memory-search-btn btn-press" onClick={handleSearch}>搜索</button>
      </div>

      {/* 搜索/分类筛选状态 */}
      {pageTitle && (
        <div className="memory-back-row">
          <button className="memory-back-btn btn-press" onClick={backToAll}>‹ 返回</button>
          <span style={{ fontSize: 14, fontWeight: 700, color: 'var(--my-ink)' }}>{pageTitle}</span>
        </div>
      )}

      {/* 分类六宫格 */}
      {!pageTitle && (
        <div className="memory-grid">
          {CATEGORIES.map((cat) => (
            <button
              key={cat.id}
              className={`memory-grid-card btn-press ${activeCategory === cat.id ? 'active' : ''}`}
              onClick={() => setActiveCategory(activeCategory === cat.id ? null : cat.id)}
            >
              <img src={cat.icon} alt="" className="memory-grid-ico" />
              <span className="memory-grid-name">{cat.label}</span>
              <span className="memory-grid-count"><b>{countByCategory(cat.id)}</b>条</span>
            </button>
          ))}
        </div>
      )}

      {/* 标签 */}
      {tags.length > 0 && !pageTitle && (
        <div className="memory-tags-row">
          {tags.map((t, i) => (
            <span key={i} className="memory-tag-chip" onClick={() => { setSearchQuery(t); loadMemories(t); setSearchMode(true); setActiveCategory(null) }}>
              #{t}
            </span>
          ))}
        </div>
      )}

      {/* 最近记忆时间线 */}
      {!pageTitle && visibleMemories.length > 0 && (
        <div className="memory-section-title">
          <span>最近记忆</span>
          <span style={{ fontSize: 11, fontWeight: 500, color: 'var(--my-ink-soft)' }}>共 {visibleMemories.length} 条</span>
        </div>
      )}
      <div className="memory-timeline">
        {visibleMemories.length === 0 ? (
          <div className="memory-empty">
            <img src="miaoyi/decor/deco_10.png" alt="" className="memory-empty-img" />
            <p>{searchMode ? '没有找到相关记忆' : '还没有记忆'}</p>
            <span>{searchMode ? '换个关键词试试，或者和喵艺多聊聊' : '和喵艺多聊聊，她会记住重要的事'}</span>
          </div>
        ) : (
          <>
            {displayedMemories.map((m) => (
              <div key={m.id} className="memory-timeline-item">
                <div className="memory-tl-content">{m.content}</div>
                <div className="memory-tl-meta">
                  <span>{formatTime(m.timestamp)}</span>
                  {m.emotion && <span className="memory-tl-emotion">#{m.emotion}</span>}
                  <button
                    className="memory-action-btn btn-press delete"
                    onClick={() => handleDelete(m.id)}
                    title="删除"
                    style={{ marginLeft: 'auto', border: 'none', background: 'none', fontSize: 14, opacity: 0.6 }}
                  ><Icon name="trash" size={15} /></button>
                </div>
              </div>
            ))}
            {visibleMemories.length > DISPLAY_CAP && (
              <div style={{ textAlign: 'center', fontSize: 12, color: 'var(--my-ink-soft)', padding: '8px 0 4px' }}>
                仅显示最近 {DISPLAY_CAP} 条，共 {visibleMemories.length} 条
              </div>
            )}
          </>
        )}
      </div>

      <div className="home-footer-pad" />
    </div>
  )
}

export default MemoryPage
