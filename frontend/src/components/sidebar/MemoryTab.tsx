import { useState } from 'react'
import type { Memory } from '../../types'
import { Icon } from '../icons'

interface MemoryTabProps {
  memories: Memory[]
  tags: string[]
  selectedTag: string | null
  onSearch: (query: string) => void
  onFilterByTag: (tag: string) => void
  onDeleteMemory: (id: string) => void
  onClearMemories: () => void
  onAddTag: (memoryId: string, tag: string) => void
  onRemoveTag: (tag: string) => void
  onEditMemory: (id: string, content: string) => void
  onTogglePinMemory: (id: string, pinned: boolean) => void
}

export function MemoryTab({ memories, tags, selectedTag, onSearch, onFilterByTag, onDeleteMemory, onClearMemories, onAddTag, onRemoveTag, onEditMemory, onTogglePinMemory }: MemoryTabProps) {
  const [searchQuery, setSearchQuery] = useState('')
  // 【L1 记忆卡片流】正在编辑的记忆 id 与草稿内容（null = 非编辑态）
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editingDraft, setEditingDraft] = useState('')

  const formatTime = (timestamp: number) => {
    if (!timestamp) return ''
    const date = new Date(timestamp)
    return `${date.getFullYear()}/${date.getMonth() + 1}/${date.getDate()} ${date.getHours()}:${date.getMinutes().toString().padStart(2, '0')}`
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      onSearch(searchQuery)
    }
  }

  const handleAddTag = (memoryId: string) => {
    const tag = prompt('给这条记忆打个标签（如：重要/喜好/事件）：')
    if (tag !== null && tag.trim()) {
      onAddTag(memoryId, tag.trim())
    }
  }

  const startEdit = (memory: Memory) => {
    setEditingId(memory.id)
    setEditingDraft(memory.content)
  }

  const cancelEdit = () => {
    setEditingId(null)
    setEditingDraft('')
  }

  const saveEdit = () => {
    const trimmed = editingDraft.trim()
    if (!editingId || !trimmed) return
    onEditMemory(editingId, trimmed)
    setEditingId(null)
    setEditingDraft('')
  }

  // 钉住的卡片置顶展示（后端已排序，前端兜底）
  const sortedMemories = [...memories].sort((a, b) => Number(b.pinned ?? false) - Number(a.pinned ?? false))

  return (
    <div className="memory-panel">
      <div className="memory-search">
        <input
          type="text"
          className="memory-search-input"
          placeholder="搜索记忆..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          onKeyDown={handleKeyDown}
        />
        <button className="memory-search-btn btn-press" onClick={() => onSearch(searchQuery)} aria-label="搜索">
          <Icon name="search" size={16} />
        </button>
      </div>
      {tags.length > 0 && (
        <div className="memory-tags">
          {tags.map((tag, idx) => (
            <span key={idx} className={`memory-tag-wrap ${selectedTag === tag ? 'active' : ''}`}>
              <button
                className="memory-tag-btn"
                onClick={() => onFilterByTag(tag)}
              >
                #{tag}
              </button>
              <button
                className="memory-tag-remove btn-press"
                onClick={(e) => { e.stopPropagation(); onRemoveTag(tag) }}
                title="移除标签"
              ><Icon name="x" size={12} /></button>
            </span>
          ))}
        </div>
      )}
      {memories.length === 0 ? (
        <div className="empty-text">还没有记忆哦~</div>
      ) : (
        <>
          <div className="memory-list">
            {sortedMemories.map((memory) => {
              const isPinned = Boolean(memory.pinned)
              const isEditing = editingId === memory.id
              return (
                <div key={memory.id} className={`memory-item memory-card ${isPinned ? 'pinned' : ''}`}>
                  {isPinned && (
                    <div className="memory-pin-badge"><Icon name="pin" size={11} /> 钉住</div>
                  )}
                  {isEditing ? (
                    <div className="memory-edit-area">
                      <textarea
                        className="memory-edit-textarea"
                        value={editingDraft}
                        maxLength={500}
                        autoFocus
                        onChange={(e) => setEditingDraft(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' && !e.shiftKey) {
                            e.preventDefault()
                            saveEdit()
                          } else if (e.key === 'Escape') {
                            cancelEdit()
                          }
                        }}
                      />
                      <div className="memory-edit-footer">
                        <span className="memory-edit-count">{editingDraft.length}/500</span>
                        <button className="memory-edit-save btn-press" onClick={saveEdit}>保存</button>
                        <button className="memory-edit-cancel btn-press" onClick={cancelEdit}>取消</button>
                      </div>
                    </div>
                  ) : (
                    <>
                      <div className="memory-content">{memory.content}</div>
                      <div className="memory-footer">
                        <div className="memory-time">{formatTime(memory.timestamp)}</div>
                        <div className="memory-actions">
                          <button
                            className={`memory-pin-btn btn-press ${isPinned ? 'active' : ''}`}
                            onClick={() => onTogglePinMemory(memory.id, !isPinned)}
                            title={isPinned ? '取消钉住' : '钉住这条记忆（喵艺会永远记住）'}
                          ><Icon name="pin" size={15} /></button>
                          <button className="memory-edit-btn btn-press" onClick={() => startEdit(memory)} title="编辑"><Icon name="edit" size={15} /></button>
                          <button className="memory-tag-btn btn-press" onClick={() => handleAddTag(memory.id)} title="打标签"><Icon name="tag" size={15} /></button>
                          <button className="memory-delete btn-press" onClick={() => onDeleteMemory(memory.id)} title="删除"><Icon name="trash" size={15} /></button>
                        </div>
                      </div>
                    </>
                  )}
                </div>
              )
            })}
          </div>
          <div className="settings-divider" />
          <button className="settings-action-btn danger btn-press" onClick={onClearMemories}>
            <Icon name="trash" size={16} /> 清除所有记忆
          </button>
        </>
      )}
    </div>
  )
}
