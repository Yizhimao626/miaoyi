import { useState, useEffect } from 'react'
import { getTheme, setTheme, applyTheme, getMute, setMute, getMinorMode, setMinorMode } from '../../utils/storage'
import { getPersonality, setPersonality, getMemories, clearMemories, clearUserProfile, exportChat, clearAllConversations, getBaseUrl, exportCharacterCard, getPlugins, togglePlugin } from '../../utils/api'
import type { PluginInfo } from '../../utils/api'
import { getAccessToken } from '../../utils/auth'
import { PRIVACY_TEXT, TERMS_TEXT } from '../../components/PrivacyModal'
import { Icon } from '../icons'
import type { PersonalityPreset } from '../../utils/api'

type ThemeType = 'light' | 'dark' | 'system'

interface SettingsTabProps {
  onShowToast: (message: string, type?: 'success' | 'error' | 'warning') => void
  onUpdateNickname: (nickname: string) => void
  onCheckin: () => void
  onUpdatePassword: (oldPassword: string, newPassword: string, confirmPassword: string) => void
}

export function SettingsTab({ onShowToast, onUpdateNickname, onCheckin, onUpdatePassword }: SettingsTabProps) {
  const [nickname, setNickname] = useState('')
  const [isMuted, setIsMuted] = useState(getMute())
  const [theme, setThemeState] = useState<ThemeType>(getTheme())
  const [showPasswordForm, setShowPasswordForm] = useState(false)
  const [oldPassword, setOldPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  
  const [currentPersonality, setCurrentPersonality] = useState<string>('default')
  const [personalityPresets, setPersonalityPresets] = useState<Record<string, PersonalityPreset>>({})
  const [selectedPersonality, setSelectedPersonality] = useState<string | null>(null)
  const [isApplyingPersonality, setIsApplyingPersonality] = useState(false)
  
  const [responseLength, setResponseLength] = useState<'short' | 'medium' | 'long'>('medium')
  const [autoContinue, setAutoContinue] = useState(true)
  // 【APK 修复】隐私/协议文档站内展示（window.open 相对路径在 WebView 中白屏）
  const [showDoc, setShowDoc] = useState<'privacy' | 'terms' | null>(null)
  const [enableSearch, setEnableSearch] = useState(true)
  const [deepThinking, setDeepThinking] = useState<'default' | 'manual' | 'always'>('default')
  
  const [themeColor, setThemeColor] = useState<'orange' | 'mint' | 'sakura' | 'starlight'>('orange')
  const [fontSize, setFontSize] = useState<'small' | 'medium' | 'large'>('medium')
  const [animationLevel, setAnimationLevel] = useState<'standard' | 'reduced' | 'none'>('standard')
  
  const [memories, setMemories] = useState<Array<{ id: string; content: string; timestamp: number }>>([])
  const [showMemories, setShowMemories] = useState(false)
  const [privacyMode, setPrivacyMode] = useState(false)
  // 【合规】未成年人模式开关（开启后 App 顶部常驻提示横幅）
  const [minorModeOn, setMinorModeOn] = useState(getMinorMode())
  const [confirmDialog, setConfirmDialog] = useState<{ type: 'clearProfile' | 'clearConversations'; onConfirm: () => void } | null>(null)
  // 【L8 插件机制】插件列表（人设扩展包）
  const [plugins, setPlugins] = useState<PluginInfo[]>([])

  useEffect(() => {
    loadPersonalityData()
    loadPlugins()
  }, [])

  const loadPlugins = async () => {
    try {
      const result = await getPlugins()
      setPlugins(result.data?.plugins || [])
    } catch {
      // 插件接口失败不影响设置页其他功能
    }
  }

  const loadPersonalityData = async () => {
    try {
      const result = await getPersonality()
      setCurrentPersonality(result.data.current)
      setPersonalityPresets(result.data.presets)
    } catch (error) {
      console.error('加载性格数据失败', error)
    }
  }

  const handleSelectPersonality = (personalityId: string) => {
    setSelectedPersonality(personalityId)
  }

  const handleApplyPersonality = async () => {
    if (!selectedPersonality || isApplyingPersonality) return
    
    setIsApplyingPersonality(true)
    try {
      const result = await setPersonality(selectedPersonality)
      onShowToast(result.data.message, 'success')
      setCurrentPersonality(selectedPersonality)
      setSelectedPersonality(null)
    } catch (error) {
      onShowToast('切换性格失败', 'error')
    } finally {
      setIsApplyingPersonality(false)
    }
  }

  const handleCancelPersonality = () => {
    setSelectedPersonality(null)
  }

  const handleToggleMute = () => {
    const newMute = !isMuted
    setIsMuted(newMute)
    setMute(newMute)
    onShowToast(newMute ? '已静音' : '已取消静音')
  }

  const handleThemeChange = (newTheme: ThemeType) => {
    // 【方案一】品牌视觉固定为梦幻浅色主题，其余选项仅保留兼容
    void newTheme
    setThemeState('light')
    setTheme('light')
    applyTheme('light')
    onShowToast('喵艺始终使用梦幻浅色主题～')
  }

  const handleSubmitPassword = () => {
    onUpdatePassword(oldPassword, newPassword, confirmPassword)
    if (showPasswordForm) {
      setShowPasswordForm(false)
      setOldPassword('')
      setNewPassword('')
      setConfirmPassword('')
    }
  }

  const handleLoadMemories = async () => {
    try {
      const result = await getMemories()
      setMemories(result.data)
      setShowMemories(true)
    } catch (error) {
      onShowToast('加载记忆失败', 'error')
    }
  }

  const handleClearProfile = async () => {
    try {
      await clearUserProfile()
      onShowToast('用户画像已清除', 'success')
      setConfirmDialog(null)
    } catch (error) {
      onShowToast('清除失败', 'error')
    }
  }

  const handleClearConversations = async () => {
    try {
      await clearAllConversations()
      onShowToast('对话记录已清除', 'success')
      setConfirmDialog(null)
    } catch (error) {
      onShowToast('清除失败', 'error')
    }
  }

  const handleExportChat = async () => {
    try {
      const result = await exportChat('txt')
      // 【APK 修复】后端返回相对路径 /exports/xxx，WebView 中 window.open 相对路径
      // 会指向不存在的 https://localhost/... → 白屏。拼上后端地址再打开。
      // 【安全修复】后端已移除裸 /exports 静态挂载，改走鉴权路由 /api/files/exports/*，
      // <a> 下载带不了 Authorization 头，用 ?token= 查询参数鉴权。
      const url = result.data.url
      let fullUrl = url
      if (url && !url.startsWith('http')) {
        const base = getBaseUrl()
        let path = url.startsWith('/') ? url : `/${url}`
        if (path.startsWith('/exports/')) {
          path = `/api/files/exports/${path.slice('/exports/'.length)}`
          const tk = getAccessToken()
          if (tk) path += `?token=${encodeURIComponent(tk)}`
        }
        if (base.startsWith('http')) {
          fullUrl = `${base}${path}`
        }
      }
      // WebView 中新窗口可能无响应，改用导航下载
      const a = document.createElement('a')
      a.href = fullUrl
      a.download = `喵艺对话_${new Date().toISOString().slice(0, 10)}.txt`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      onShowToast('导出成功！请在下载/文件管理查看', 'success')
    } catch (error) {
      onShowToast('导出失败', 'error')
    }
  }

  /** 【L8】导出 SillyTavern V2 角色卡（下载模式复用 handleExportChat 的鉴权下载逻辑） */
  const handleExportCard = async () => {
    try {
      const result = await exportCharacterCard()
      const url = result.data.url
      let fullUrl = url
      if (url && !url.startsWith('http')) {
        const base = getBaseUrl()
        let path = url.startsWith('/') ? url : `/${url}`
        if (path.startsWith('/exports/')) {
          path = `/api/files/exports/${path.slice('/exports/'.length)}`
          const tk = getAccessToken()
          if (tk) path += `?token=${encodeURIComponent(tk)}`
        }
        if (base.startsWith('http')) {
          fullUrl = `${base}${path}`
        }
      }
      const a = document.createElement('a')
      a.href = fullUrl
      a.download = url?.split('/').pop() || 'character_card.json'
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      onShowToast('角色卡导出成功！可导入 SillyTavern 等应用', 'success')
    } catch {
      onShowToast('导出角色卡失败', 'error')
    }
  }

  /** 【L8】切换插件启用状态（乐观更新，失败回滚） */
  const handleTogglePlugin = async (p: PluginInfo) => {
    const next = !p.enabled
    setPlugins((prev) => prev.map((x) => (x.id === p.id ? { ...x, enabled: next } : x)))
    try {
      await togglePlugin(p.id, next)
      onShowToast(next ? `「${p.name}」已启用` : `「${p.name}」已停用`, 'success')
    } catch {
      setPlugins((prev) => prev.map((x) => (x.id === p.id ? { ...x, enabled: !next } : x)))
      onShowToast('切换失败', 'error')
    }
  }

  const handleConfirmClearProfile = () => {
    setConfirmDialog({
      type: 'clearProfile',
      onConfirm: handleClearProfile,
    })
  }

  const handleConfirmClearConversations = () => {
    setConfirmDialog({
      type: 'clearConversations',
      onConfirm: handleClearConversations,
    })
  }

  const handleCancelConfirm = () => {
    setConfirmDialog(null)
  }

  const handleDeleteMemory = async (id: string) => {
    try {
      await clearMemories()
      setMemories(prev => prev.filter(m => m.id !== id))
      onShowToast('记忆已删除', 'success')
    } catch (error) {
      onShowToast('删除失败', 'error')
    }
  }

  return (
    <div className="settings-panel">
      <div className="settings-item">
        <span className="settings-label">修改昵称</span>
        <input
          type="text"
          className="settings-input"
          placeholder="输入新昵称"
          value={nickname}
          onChange={(e) => setNickname(e.target.value)}
        />
        <button className="settings-btn btn-press" onClick={() => onUpdateNickname(nickname)}>
          保存
        </button>
      </div>
      <div className="settings-divider" />
      <button className="settings-action-btn checkin btn-press" onClick={onCheckin}>
        <Icon name="bell" size={16} /> 每日签到
      </button>
      <div className="settings-divider" />
      {showPasswordForm ? (
        <div className="password-form">
          <div className="settings-item">
            <span className="settings-label">原密码</span>
            <input
              type="password"
              className="settings-input"
              placeholder="请输入原密码"
              value={oldPassword}
              onChange={(e) => setOldPassword(e.target.value)}
            />
          </div>
          <div className="settings-item">
            <span className="settings-label">新密码</span>
            <input
              type="password"
              className="settings-input"
              placeholder="请输入新密码(含字母和数字)"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
            />
          </div>
          <div className="settings-item">
            <span className="settings-label">确认密码</span>
            <input
              type="password"
              className="settings-input"
              placeholder="请再次输入新密码"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
            />
          </div>
          <div className="feedback-actions">
            <button className="settings-btn btn-press" onClick={() => {
              setShowPasswordForm(false)
              setOldPassword('')
              setNewPassword('')
              setConfirmPassword('')
            }}>取消</button>
            <button className="settings-btn checkin btn-press" onClick={handleSubmitPassword}>确认修改</button>
          </div>
        </div>
      ) : (
        <button className="settings-action-btn btn-press" onClick={() => setShowPasswordForm(true)}>
          <Icon name="lock" size={16} /> 修改密码
        </button>
      )}
      <div className="settings-divider" />
      <button className="settings-action-btn btn-press" onClick={handleToggleMute}>
        <Icon name={isMuted ? 'volumeOff' : 'volume'} size={16} /> {isMuted ? '已静音' : '正常'}
      </button>
      <div className="settings-divider" />
      <div className="settings-item">
        <span className="settings-label">主题模式</span>
        <div style={{ display: 'flex', gap: '8px' }}>
          <button
            className={`settings-action-btn btn-press ${theme === 'light' ? 'checkin' : ''}`}
            onClick={() => handleThemeChange('light')}
          >
            <Icon name="sun" size={15} /> 浅色
          </button>
          <button
            className={`settings-action-btn btn-press ${theme === 'dark' ? 'checkin' : ''}`}
            onClick={() => handleThemeChange('dark')}
          >
            <Icon name="moon" size={15} /> 深色
          </button>
          <button
            className={`settings-action-btn btn-press ${theme === 'system' ? 'checkin' : ''}`}
            onClick={() => handleThemeChange('system')}
          >
            <Icon name="smartphone" size={15} /> 跟随系统
          </button>
        </div>
      </div>
      <div className="settings-divider" />
      <div className="settings-section">
        <div className="settings-section-title"><Icon name="cat" size={16} /> 喵艺性格</div>
        <div className="settings-section-desc">选择不同的性格，喵艺会有不一样的说话风格喵~</div>
        
        {selectedPersonality && (
          <div className="personality-preview">
            <div className="personality-preview-header">
              <span className="preview-emoji">{personalityPresets[selectedPersonality]?.emoji}</span>
              <span className="preview-name">{personalityPresets[selectedPersonality]?.name}</span>
            </div>
            <div className="personality-preview-desc">{personalityPresets[selectedPersonality]?.description}</div>
            <div className="personality-preview-tone">语气特点：{personalityPresets[selectedPersonality]?.tone}</div>
            <div className="preview-actions">
              <button className="preview-btn cancel btn-press" onClick={handleCancelPersonality}>取消</button>
              <button className="preview-btn confirm btn-press" onClick={handleApplyPersonality} disabled={isApplyingPersonality}>
                {isApplyingPersonality ? '切换中...' : '确认切换'}
              </button>
            </div>
          </div>
        )}
        
        <div className="personality-grid">
          {Object.entries(personalityPresets).map(([key, preset]) => (
            <button
              key={key}
              className={`personality-card btn-press ${currentPersonality === key ? 'active' : ''} ${selectedPersonality === key ? 'selected' : ''}`}
              onClick={() => handleSelectPersonality(key)}
            >
              <div className="personality-emoji">{preset.emoji}</div>
              <div className="personality-name">{preset.name}</div>
              <div className="personality-desc">{preset.description}</div>
              {currentPersonality === key && (
                <div className="personality-check"><Icon name="check" size={14} /></div>
              )}
            </button>
          ))}
        </div>
      </div>
      
      <div className="settings-section">
        <div className="settings-section-title"><Icon name="messageSquare" size={16} /> 对话设置</div>
        <div className="settings-section-desc">调整喵艺的回复方式和行为喵~</div>
        
        <div className="settings-item">
          <span className="settings-label">回复长度</span>
          <div className="settings-select-group">
            {[
              { value: 'short', label: '短' },
              { value: 'medium', label: '中' },
              { value: 'long', label: '长' },
            ].map((item) => (
              <button
                key={item.value}
                className={`settings-select-btn btn-press ${responseLength === item.value ? 'active' : ''}`}
                onClick={() => setResponseLength(item.value as 'short' | 'medium' | 'long')}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>
        
        <div className="settings-item">
          <span className="settings-label">自动继续</span>
          <label className="toggle-switch">
            <input
              type="checkbox"
              checked={autoContinue}
              onChange={(e) => setAutoContinue(e.target.checked)}
            />
            <span className="toggle-slider"></span>
          </label>
          <span className="settings-hint">回复被截断时自动生成后续内容</span>
        </div>
        
        <div className="settings-item">
          <span className="settings-label">联网搜索</span>
          <label className="toggle-switch">
            <input
              type="checkbox"
              checked={enableSearch}
              onChange={(e) => setEnableSearch(e.target.checked)}
            />
            <span className="toggle-slider"></span>
          </label>
          <span className="settings-hint">关闭后只使用模型自身知识</span>
        </div>

        <div className="settings-item">
          <span className="settings-label"><Icon name="shield" size={14} /> 未成年人模式</span>
          <label className="toggle-switch">
            <input
              type="checkbox"
              checked={minorModeOn}
              onChange={(e) => {
                const on = e.target.checked
                setMinorModeOn(on)
                setMinorMode(on)
                // 通知 App 顶栏横幅即时显示/隐藏
                window.dispatchEvent(new CustomEvent('miaoyi_minor_mode_changed', { detail: on }))
                onShowToast(on ? '未成年人模式已开启' : '未成年人模式已关闭', 'success')
              }}
            />
            <span className="toggle-slider"></span>
          </label>
          <span className="settings-hint">为未成年用户启用适龄内容保护</span>
        </div>
        
        <div className="settings-item">
          <span className="settings-label">深度思考模式</span>
          <div className="settings-select-group">
            {[
              { value: 'default', label: '默认' },
              { value: 'manual', label: '手动' },
              { value: 'always', label: '始终' },
            ].map((item) => (
              <button
                key={item.value}
                className={`settings-select-btn btn-press ${deepThinking === item.value ? 'active' : ''}`}
                onClick={() => setDeepThinking(item.value as 'default' | 'manual' | 'always')}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>
      </div>
      
      <div className="settings-section">
        <div className="settings-section-title"><Icon name="palette" size={16} /> 外观与主题</div>
        <div className="settings-section-desc">打造专属你的喵艺界面喵~</div>
        
        <div className="settings-item">
          <span className="settings-label">主题色</span>
          <div className="theme-color-grid">
            {[
              { value: 'orange', label: '暖橘', color: '#FF9A76' },
              { value: 'mint', label: '薄荷绿', color: '#7ED957' },
              { value: 'sakura', label: '樱花粉', color: '#FFB7C5' },
              { value: 'starlight', label: '星空紫', color: '#9B59B6' },
            ].map((item) => (
              <button
                key={item.value}
                className={`theme-color-btn btn-press ${themeColor === item.value ? 'active' : ''}`}
                style={{ backgroundColor: item.color }}
                onClick={() => setThemeColor(item.value as 'orange' | 'mint' | 'sakura' | 'starlight')}
                title={item.label}
              >
                {themeColor === item.value && <span className="theme-check"><Icon name="check" size={12} strokeWidth={2.6} /></span>}
              </button>
            ))}
          </div>
        </div>
        
        <div className="settings-item">
          <span className="settings-label">字体大小</span>
          <div className="settings-select-group">
            {[
              { value: 'small', label: '小', emoji: 'A' },
              { value: 'medium', label: '中', emoji: 'A' },
              { value: 'large', label: '大', emoji: 'A' },
            ].map((item, index) => (
              <button
                key={item.value}
                className={`settings-select-btn btn-press ${fontSize === item.value ? 'active' : ''}`}
                onClick={() => setFontSize(item.value as 'small' | 'medium' | 'large')}
                style={{ fontSize: `${14 + index * 4}px`, fontWeight: fontSize === item.value ? 'bold' : 'normal' }}
              >
                {item.emoji}
              </button>
            ))}
          </div>
        </div>
        
        <div className="settings-item">
          <span className="settings-label">动画效果</span>
          <div className="settings-select-group">
            {[
              { value: 'standard', label: '标准' },
              { value: 'reduced', label: '减弱' },
              { value: 'none', label: '无' },
            ].map((item) => (
              <button
                key={item.value}
                className={`settings-select-btn btn-press ${animationLevel === item.value ? 'active' : ''}`}
                onClick={() => setAnimationLevel(item.value as 'standard' | 'reduced' | 'none')}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>
      </div>
      
      <div className="settings-section">
        <div className="settings-section-title"><Icon name="brain" size={16} /> 记忆与隐私</div>
        <div className="settings-section-desc">管理喵艺记住的事情，保护你的隐私喵~</div>
        
        <button className="settings-action-btn btn-press" onClick={handleLoadMemories}>
          <Icon name="eye" size={16} /> 查看我的画像
        </button>
        
        <button className="settings-action-btn btn-press danger" onClick={handleConfirmClearProfile}>
          <Icon name="trash" size={16} /> 清除我的画像
        </button>
        
        <button className="settings-action-btn btn-press danger" onClick={handleConfirmClearConversations}>
          <Icon name="trash" size={16} /> 清除对话记录
        </button>
        
        <button className="settings-action-btn btn-press" onClick={handleExportChat}>
          <Icon name="download" size={16} /> 导出对话记录
        </button>

        {/* 【L8 角色卡导出】SillyTavern V2 兼容 */}
        <button className="settings-action-btn btn-press" onClick={handleExportCard}>
          <Icon name="fileText" size={16} /> 导出喵艺角色卡
        </button>

        {/* 【L8 插件机制】人设扩展包管理 */}
        {plugins.length > 0 && (
          <details className="settings-item" style={{ flexDirection: 'column', alignItems: 'stretch' }}>
            <summary className="settings-label" style={{ cursor: 'pointer' }}>
              <Icon name="puzzle" size={15} /> 插件（{plugins.filter((p) => p.enabled).length}/{plugins.length} 启用）
            </summary>
            <div style={{ marginTop: 8 }}>
              {plugins.map((p) => (
                <div key={p.id} style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  gap: 8, padding: '8px 10px', margin: '4px 0', borderRadius: 10,
                  background: 'rgba(148,163,184,0.08)',
                }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 13, fontWeight: 500 }}>
                      {p.name} <span style={{ fontSize: 10, opacity: 0.5 }}>v{p.version}</span>
                    </div>
                    <div style={{ fontSize: 11, opacity: 0.6, overflowWrap: 'anywhere' }}>{p.description}</div>
                  </div>
                  <label className="toggle-switch" style={{ flexShrink: 0 }}>
                    <input type="checkbox" checked={p.enabled} onChange={() => handleTogglePlugin(p)} />
                    <span className="toggle-slider"></span>
                  </label>
                </div>
              ))}
            </div>
          </details>
        )}

        <div className="settings-item">
          <span className="settings-label">隐私模式</span>
          <label className="toggle-switch">
            <input
              type="checkbox"
              checked={privacyMode}
              onChange={(e) => setPrivacyMode(e.target.checked)}
            />
            <span className="toggle-slider"></span>
          </label>
          <span className="settings-hint">开启后不记录任何信息，退出即焚</span>
        </div>
        
        {showMemories && memories.length > 0 && (
          <div className="memories-list">
            <div className="memories-header">
              <span>喵艺记住的事情</span>
              <button className="memories-close btn-press" onClick={() => setShowMemories(false)} aria-label="关闭"><Icon name="x" size={14} /></button>
            </div>
            {memories.map((memory) => (
              <div key={memory.id} className="memory-card">
                <span className="memory-content">{memory.content}</span>
                <button className="memory-delete btn-press" onClick={() => handleDeleteMemory(memory.id)} aria-label="删除记忆"><Icon name="trash" size={15} /></button>
              </div>
            ))}
          </div>
        )}
        
        {showMemories && memories.length === 0 && (
          <div className="empty-state">
            <Icon name="cat" size={18} /> 喵艺还没记住任何事情呢...多和我聊聊吧喵~
          </div>
        )}
      </div>
      
      <div className="settings-section">
        <div className="settings-section-title"><Icon name="book" size={16} /> 关于喵艺</div>
        <div className="settings-section-desc">用爱猫的心打造的AI伙伴喵~</div>
        
        <div className="about-card">
          <div className="about-version"><Icon name="paw" size={13} /> v1.0.0</div>
          <div className="about-creator">
            <span className="creator-icon"><Icon name="user" size={16} /></span>
            <span>开发者：一只爱猫的程序员</span>
          </div>
          <div className="about-slogan">用爱猫的心打造</div>
        </div>
        
        <button className="settings-action-btn btn-press" onClick={() => window.open('mailto:feedback@miaoyi.ai')}>
          <Icon name="messageSquare" size={16} /> 反馈与建议
        </button>
        
        <button className="settings-action-btn btn-press" onClick={() => setShowDoc('privacy')}>
          <Icon name="fileText" size={16} /> 隐私政策
        </button>
        
        <button className="settings-action-btn btn-press" onClick={() => setShowDoc('terms')}>
          <Icon name="clipboard" size={16} /> 用户协议
        </button>
      </div>

      {/* 【APK 修复】隐私/协议文档站内模态（WebView 中 window.open 相对路径不可用） */}
      {showDoc && (
        <div className="privacy-modal-overlay show" onClick={() => setShowDoc(null)}>
          <div className="privacy-modal doc-modal" onClick={e => e.stopPropagation()}>
            <h3 className="privacy-modal-title">
              <Icon name={showDoc === 'privacy' ? 'fileText' : 'clipboard'} size={17} />
              {showDoc === 'privacy' ? '隐私政策' : '用户协议'}
            </h3>
            <div className="privacy-doc-content">
              <pre className="privacy-doc-pre">{showDoc === 'privacy' ? PRIVACY_TEXT : TERMS_TEXT}</pre>
            </div>
            <button className="privacy-btn accept btn-press" onClick={() => setShowDoc(null)}>
              关闭
            </button>
          </div>
        </div>
      )}
      
      {confirmDialog && (
        <div className="confirm-dialog-overlay">
          <div className="confirm-dialog">
            <div className="confirm-icon">
              <Icon name="alert" size={30} />
            </div>
            <div className="confirm-title">
              {confirmDialog.type === 'clearProfile' ? '确认清除画像' : '确认清除对话'}
            </div>
            <div className="confirm-message">
              {confirmDialog.type === 'clearProfile' 
                ? '真的要忘记关于你的一切喵？我会伤心的...但如果你坚持，我会尊重你的选择喵~' 
                : '确定要清除所有对话记录吗？喵艺会保留对你的记忆喵~'}
            </div>
            <div className="confirm-actions">
              <button className="confirm-btn cancel btn-press" onClick={handleCancelConfirm}>取消</button>
              <button className="confirm-btn confirm btn-press" onClick={confirmDialog.onConfirm}>确认</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}