import { useState, useEffect } from 'react'
import type { GrowthData, Memory, EmotionalState, UserProfile } from '../types'
import { getMemories, deleteMemory, updateNickname, updatePassword, checkin, clearMemories, searchMemories, getMemoryTags, getMemoriesByTag, getRelationshipStatus, getRelationshipAnniversary, submitFeedback, getProfile, addMemoryTag, removeMemoryTag, editMemory, toggleMemoryPin } from '../utils/api'
import { ProfileTab } from './sidebar/ProfileTab'
import { GrowthTab } from './sidebar/GrowthTab'
import { MemoryTab } from './sidebar/MemoryTab'
import { DiaryTab } from './sidebar/DiaryTab'
import { SettingsTab } from './sidebar/SettingsTab'
import { AboutTab } from './sidebar/AboutTab'
import { EmotionalAvatar, IntimacyLevel } from './EmotionComponents'
import { Icon } from './icons'

interface SidebarProps {
  isOpen: boolean
  onClose: () => void
  growthData: GrowthData
  emotionalState: EmotionalState
  onShowToast: (message: string, type?: 'success' | 'error' | 'warning') => void
  onLogout: () => void
}

type TabType = 'profile' | 'growth' | 'memory' | 'diary' | 'settings' | 'about'

interface RelationshipStatus {
  stage: string
  days: number
  milestone: string
}

interface Anniversary {
  next_date: string
  days_left: number
}

export function Sidebar({ isOpen, onClose, growthData, emotionalState, onShowToast, onLogout }: SidebarProps) {
  const [activeTab, setActiveTab] = useState<TabType>('profile')
  const [memories, setMemories] = useState<Memory[]>([])
  const [tags, setTags] = useState<string[]>([])
  const [selectedTag, setSelectedTag] = useState<string | null>(null)
  const [relationshipStatus, setRelationshipStatus] = useState<RelationshipStatus | null>(null)
  const [anniversary, setAnniversary] = useState<Anniversary | null>(null)
  const [userProfile, setUserProfile] = useState<UserProfile | null>(null)

  useEffect(() => {
    if (isOpen && activeTab === 'memory') {
      loadMemories()
      loadTags()
    }
    if (isOpen && activeTab === 'growth') {
      loadRelationshipStatus()
    }
    if (isOpen && activeTab === 'profile') {
      loadUserProfile()
    }
  }, [isOpen, activeTab])

  const loadUserProfile = async () => {
    try {
      const result = await getProfile()
      setUserProfile(result.data || null)
    } catch {
      onShowToast('加载用户信息失败', 'error')
    }
  }

  const loadMemories = async () => {
    try {
      const result = await getMemories()
      setMemories(result.data || [])
    } catch {
      onShowToast('加载记忆失败', 'error')
    }
  }

  const loadTags = async () => {
    try {
      const result = await getMemoryTags()
      setTags(result.data || [])
    } catch {
      onShowToast('加载标签失败', 'error')
    }
  }

  const loadRelationshipStatus = async () => {
    try {
      const statusResult = await getRelationshipStatus()
      setRelationshipStatus(statusResult.data || null)
      const anniversaryResult = await getRelationshipAnniversary()
      setAnniversary(anniversaryResult.data || null)
    } catch {
      onShowToast('加载关系状态失败', 'error')
    }
  }

  const handleSearchMemories = async (query: string) => {
    if (!query.trim()) {
      loadMemories()
      return
    }
    try {
      const result = await searchMemories(query)
      setMemories(result.data || [])
    } catch {
      onShowToast('搜索失败', 'error')
    }
  }

  const handleFilterByTag = async (tag: string) => {
    if (selectedTag === tag) {
      setSelectedTag(null)
      loadMemories()
    } else {
      setSelectedTag(tag)
      try {
        const result = await getMemoriesByTag(tag)
        setMemories(result.data || [])
      } catch {
        onShowToast('筛选失败', 'error')
      }
    }
  }

  const handleDeleteMemory = async (id: string) => {
    if (!confirm('确定要删除这条记忆吗？')) return
    try {
      await deleteMemory(id)
      setMemories((prev) => prev.filter((m) => m.id !== id))
      onShowToast('记忆已删除', 'success')
    } catch {
      onShowToast('删除失败', 'error')
    }
  }

  const handleEditMemory = async (id: string, content: string) => {
    try {
      await editMemory(id, content)
      setMemories((prev) => prev.map((m) => (m.id === id ? { ...m, content } : m)))
      onShowToast('记忆已更新喵～', 'success')
    } catch (err: any) {
      onShowToast(err?.message || '更新失败', 'error')
    }
  }

  const handleTogglePinMemory = async (id: string, pinned: boolean) => {
    try {
      await toggleMemoryPin(id, pinned)
      // 钉住状态变化后重新拉取，让后端排序（钉住置顶）生效
      await loadMemories()
      onShowToast(pinned ? '已钉住，喵艺会永远记住这条喵～' : '已取消钉住', 'success')
    } catch (err: any) {
      onShowToast(err?.message || '操作失败', 'error')
    }
  }

  const handleAddTag = async (memoryId: string, tag: string) => {
    const trimmed = tag.trim()
    if (!trimmed) { onShowToast('标签不能为空', 'warning'); return }
    try {
      await addMemoryTag(Number(memoryId), trimmed)
      onShowToast('标签已添加喵～', 'success')
      loadTags()
    } catch {
      onShowToast('添加标签失败', 'error')
    }
  }

  const handleRemoveTag = async (tag: string) => {
    if (!confirm(`移除标签 #${tag}？将从所有相关记忆上移除该标签`)) return
    try {
      const res = await getMemoriesByTag(tag)
      const memories = res.data || []
      for (const m of memories) {
        if (m.id != null) {
          await removeMemoryTag(Number(m.id), tag)
        }
      }
      onShowToast('标签已移除喵～', 'success')
      loadTags()
      loadMemories()
    } catch {
      onShowToast('移除标签失败', 'error')
    }
  }

  const handleClearMemory = async () => {
    if (!confirm('确定要清除所有记忆吗？此操作不可恢复！')) return
    try {
      await clearMemories()
      onShowToast('记忆已清除', 'success')
      setMemories([])
    } catch {
      onShowToast('清除失败', 'error')
    }
  }

  const handleUpdateNickname = async (nickname: string) => {
    if (!nickname.trim()) {
      onShowToast('请输入昵称', 'warning')
      return
    }
    try {
      await updateNickname(nickname)
      onShowToast('昵称修改成功', 'success')
      loadUserProfile()
    } catch {
      onShowToast('修改失败', 'error')
    }
  }

  const handleCheckin = async () => {
    try {
      const result = await checkin()
      onShowToast(result.message || '签到成功！', 'success')
    } catch (err) {
      onShowToast((err as Error).message || '签到失败', 'error')
    }
  }

  const handleUpdatePassword = async (oldPassword: string, newPassword: string, confirmPassword: string) => {
    if (!oldPassword || !newPassword || !confirmPassword) {
      onShowToast('请填写所有字段', 'warning')
      return
    }
    if (newPassword !== confirmPassword) {
      onShowToast('新密码和确认密码不一致', 'warning')
      return
    }
    try {
      await updatePassword(oldPassword, newPassword)
      onShowToast('密码修改成功', 'success')
    } catch (err) {
      onShowToast((err as Error).message || '修改失败', 'error')
    }
  }

  const handleSubmitFeedback = async (content: string) => {
    await submitFeedback(content)
  }

  const handleLogout = () => {
    if (!confirm('确定要退出登录吗？')) return
    // token/数据清理统一由 App.handleLogout → cleanupUserData 负责
    //（旧版这里调的 clearAuth 清的是废弃的 miaoyi_token 键，对现行 token 是 no-op）
    onLogout()
  }

  const renderTab = () => {
    switch (activeTab) {
      case 'profile':
        return (
          <ProfileTab
            growthData={growthData}
            userProfile={userProfile}
            onCheckin={handleCheckin}
            onNavigate={(tab) => setActiveTab(tab as TabType)}
            onLogout={handleLogout}
          />
        )
      case 'growth':
        return (
          <GrowthTab
            growthData={growthData}
            emotionalState={emotionalState}
            relationshipStatus={relationshipStatus}
            anniversary={anniversary}
          />
        )
      case 'memory':
        return (
          <MemoryTab
            memories={memories}
            tags={tags}
            selectedTag={selectedTag}
            onSearch={handleSearchMemories}
            onFilterByTag={handleFilterByTag}
            onDeleteMemory={handleDeleteMemory}
            onClearMemories={handleClearMemory}
            onAddTag={handleAddTag}
            onRemoveTag={handleRemoveTag}
            onEditMemory={handleEditMemory}
            onTogglePinMemory={handleTogglePinMemory}
          />
        )
      case 'diary':
        return (
          <DiaryTab
            onShowToast={onShowToast}
          />
        )
      case 'settings':
        return (
          <SettingsTab
            onShowToast={onShowToast}
            onUpdateNickname={handleUpdateNickname}
            onCheckin={handleCheckin}
            onUpdatePassword={handleUpdatePassword}
          />
        )
      case 'about':
        return (
          <AboutTab
            onSubmitFeedback={handleSubmitFeedback}
            onShowToast={onShowToast}
          />
        )
      default:
        return null
    }
  }

  return (
    <>
      <div className={`sidebar-mask sidebar-own-mask ${isOpen ? 'show' : ''}`} onClick={onClose} />
      <div className={`sidebar ${isOpen ? 'show' : ''}`}>
        <div className="sidebar-header">
          <h2 className="sidebar-title">
            <Icon name="cat" size={20} />
            <span>喵艺小屋</span>
          </h2>
          <button className="sidebar-close btn-press" onClick={onClose} aria-label="关闭">
            <Icon name="x" size={18} />
          </button>
        </div>
        {/* 【第4步】情绪状态卡：聊天页中间状态行收纳进右侧面板 */}
        <div className="sidebar-emotion-card">
          <EmotionalAvatar
            emotion={emotionalState.dominant_emotion || 'content'}
            valence={emotionalState.valence}
            arousal={emotionalState.arousal}
            intimacy={emotionalState.intimacy}
            trust={emotionalState.trust}
            size={44}
            animated={true}
          />
          <div className="sidebar-emotion-info">
            <span className="sidebar-emotion-label">
              {emotionalState.dominant_emoji || '😊'} {emotionalState.dominant_label || '平静'}
            </span>
            <IntimacyLevel level={growthData.intimacy / 100} showLabel={true} />
          </div>
          <div className="sidebar-emotion-lv">
            Lv.{growthData.level}
            <span>{growthData.intimacy}%</span>
          </div>
        </div>
        <div className="sidebar-tabs">
          <button
            className={`sidebar-tab btn-press ${activeTab === 'profile' ? 'active' : ''}`}
            onClick={() => setActiveTab('profile')}
          >
            <Icon name="user" size={15} />
            <span>我的</span>
          </button>
          <button
            className={`sidebar-tab btn-press ${activeTab === 'growth' ? 'active' : ''}`}
            onClick={() => setActiveTab('growth')}
          >
            <Icon name="trendingUp" size={15} />
            <span>成长</span>
          </button>
          <button
            className={`sidebar-tab btn-press ${activeTab === 'memory' ? 'active' : ''}`}
            onClick={() => setActiveTab('memory')}
          >
            <Icon name="brain" size={15} />
            <span>记忆</span>
          </button>
          <button
            className={`sidebar-tab btn-press ${activeTab === 'diary' ? 'active' : ''}`}
            onClick={() => setActiveTab('diary')}
          >
            <Icon name="book" size={15} />
            <span>日记</span>
          </button>
          <button
            className={`sidebar-tab btn-press ${activeTab === 'settings' ? 'active' : ''}`}
            onClick={() => setActiveTab('settings')}
          >
            <Icon name="settings" size={15} />
            <span>设置</span>
          </button>
          <button
            className={`sidebar-tab btn-press ${activeTab === 'about' ? 'active' : ''}`}
            onClick={() => setActiveTab('about')}
          >
            <Icon name="info" size={15} />
            <span>关于</span>
          </button>
        </div>
        <div className="sidebar-content">{renderTab()}</div>
      </div>
    </>
  )
}