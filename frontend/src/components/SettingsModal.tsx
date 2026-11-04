import { useState, useEffect } from 'react'
import { getTheme, setTheme, applyTheme, setNickname } from '../utils/storage'
import { logout as authLogout } from '../utils/auth'
import { getProfile, updateNickname, updatePassword, logoutApi, clearUserProfile, getBaseUrl, setBaseUrl, clearAllConversations, getVoiceMode, setVoiceMode } from '../utils/api'
import { useUserStore } from '../store/userStore'
import { Icon } from './icons'

type ThemeMode = 'light' | 'dark' | 'system'
type FontSize = 'small' | 'medium' | 'large'

interface SettingsModalProps {
  isOpen: boolean
  onClose: () => void
  onShowToast: (message: string, type?: 'success' | 'error' | 'warning') => void
  onThemeChange?: () => void
  /** 打开时是否直接展开"服务器地址"弹窗（断连引导场景） */
  openServerModal?: boolean
}

export function SettingsModal({ isOpen, onClose, onShowToast, onThemeChange, openServerModal }: SettingsModalProps) {
  const { userProfile, setUserProfile } = useUserStore()

  const [theme, setThemeState] = useState<ThemeMode>(getTheme())
  const [fontSize, setFontSize] = useState<FontSize>(
    (localStorage.getItem('miaoyi_font_size') as FontSize) || 'medium'
  )
  const [notifEnabled] = useState(
    localStorage.getItem('miaoyi_notif') !== 'false'
  )
  const [soundEnabled, setSoundEnabled] = useState(
    localStorage.getItem('miaoyi_sound') !== 'false'
  )
  const [showAbout, setShowAbout] = useState(false)
  const [showServerModal, setShowServerModal] = useState(false)
  const [serverUrlInput, setServerUrlInput] = useState('')
  // 语音声音切换
  const [voiceMode, setVoiceModeState] = useState(1)
  const [voiceModeName, setVoiceModeName] = useState('晓晓女声')
  const [showVoiceOptions, setShowVoiceOptions] = useState(false)

  const VOICE_OPTIONS = [
    { mode: 1, name: '晓晓女声', desc: '在线自然女声（配代理可用）' },
    { mode: 0, name: '本地女声', desc: '本地合成女声' },
    { mode: 2, name: '云希男声', desc: '在线男声（配代理可用）' },
    { mode: 3, name: '混合模式', desc: '晓晓优先，失败自动降级' },
  ]

  // 账号管理相关状态
  const [editingNickname, setEditingNickname] = useState(false)
  const [nicknameInput, setNicknameInput] = useState('')
  const [savingNickname, setSavingNickname] = useState(false)
  const [showPasswordModal, setShowPasswordModal] = useState(false)
  const [oldPwd, setOldPwd] = useState('')
  const [newPwd, setNewPwd] = useState('')
  const [confirmPwd, setConfirmPwd] = useState('')
  const [changingPassword, setChangingPassword] = useState(false)
  const [loggingOut, setLoggingOut] = useState(false)

  // 辅助函数：手机号脱敏
  function maskPhone(phone?: string): string {
    if (!phone) return '-'
    if (phone.length < 7) return phone
    return phone.slice(0, 3) + '****' + phone.slice(-4)
  }

  // 打开时拉取最新 profile 与当前声音
  useEffect(() => {
    if (!isOpen) return
    getProfile()
      .then(res => setUserProfile(res.data))
      .catch(() => {/* 静默：store 中可能已有旧数据可展示 */})
    getVoiceMode()
      .then((res: any) => {
        const data = res.data || {}
        if (typeof data.voice_mode === 'number') setVoiceModeState(data.voice_mode)
        if (data.voice_mode_name) setVoiceModeName(data.voice_mode_name)
      })
      .catch(() => {})
  }, [isOpen, setUserProfile])

  // 【断连引导】openServerModal 为 true 时自动展开服务器地址弹窗
  useEffect(() => {
    if (isOpen && openServerModal) {
      setServerUrlInput(getBaseUrl() === '/api' ? '' : getBaseUrl())
      setShowServerModal(true)
    }
  }, [isOpen, openServerModal])

  if (!isOpen) return null

  const handleThemeChange = (mode: ThemeMode) => {
    // 【方案一】品牌视觉固定为梦幻浅色主题，其余选项仅保留兼容
    void mode
    setThemeState('light')
    setTheme('light')
    applyTheme('light')
    onThemeChange?.()
    onShowToast('喵艺始终使用梦幻浅色主题～', 'success')
  }

  const handleFontSizeChange = (size: FontSize) => {
    setFontSize(size)
    localStorage.setItem('miaoyi_font_size', size)
    const root = document.documentElement
    root.style.setProperty('--miaoyi-font-size-base', size === 'small' ? '13px' : size === 'large' ? '17px' : '15px')
    onShowToast('字体大小已调整', 'success')
  }

  const handleToggleNotif = async () => {
    // 【APK 环境】Capacitor WebView 不支持 Web Push（需原生插件 + FCM，国内不可用），
    // 开关已禁用。保留此函数仅为未来接入原生推送预留。
    onShowToast('消息通知暂不支持，APK 环境无法接收推送喵～', 'warning')
  }

  const handleToggleSound = () => {
    const next = !soundEnabled
    setSoundEnabled(next)
    localStorage.setItem('miaoyi_sound', String(next))
    onShowToast(`提示音已${next ? '开启' : '关闭'}`, 'success')
  }

  const handleClearCache = () => {
    // 清除聊天缓存但保留登录态和设置
    const keepKeys = ['miaoyi_theme', 'miaoyi_font_size', 'miaoyi_notif', 'miaoyi_sound',
      'miaoyi_access_token', 'miaoyi_refresh_token', 'miaoyi_onboarding_shown',
      'miaoyi_user_info', 'miaoyi_nickname']
    const saved: Record<string, string> = {}
    keepKeys.forEach(k => {
      const v = localStorage.getItem(k)
      if (v) saved[k] = v
    })
    localStorage.clear()
    Object.entries(saved).forEach(([k, v]) => localStorage.setItem(k, v))
    onShowToast('缓存已清除', 'success')
    setTimeout(() => window.location.reload(), 800)
  }

  const handleClearConversations = async () => {
    if (!confirm('确定清除所有聊天记录吗？此操作不可恢复喵～')) return
    try {
      await clearAllConversations()
      onShowToast('聊天记录已清除喵～', 'success')
    } catch (e) {
      onShowToast('清除失败喵...', 'error')
    }
  }

  const handleVoiceChange = async (mode: number) => {
    try {
      const result = await setVoiceMode(mode)
      const data = result.data as any
      setVoiceModeState(data?.voice_mode ?? mode)
      setVoiceModeName(data?.voice_mode_name || VOICE_OPTIONS.find(o => o.mode === mode)?.name || '未知')
      setShowVoiceOptions(false)
      onShowToast(`声音已切换为「${VOICE_OPTIONS.find(o => o.mode === mode)?.name || mode}」喵～`, 'success')
    } catch {
      onShowToast('切换声音失败喵...', 'error')
    }
  }

  const handleOpenServerModal = () => {
    setServerUrlInput(getBaseUrl() === '/api' ? '' : getBaseUrl())
    setShowServerModal(true)
  }

  const handleSaveServerUrl = () => {
    const url = serverUrlInput.trim()
    if (url && !/^https?:\/\//.test(url)) {
      onShowToast('请输入以 http:// 或 https:// 开头的地址喵～', 'warning')
      return
    }
    setBaseUrl(url || '/api')
    setShowServerModal(false)
    onShowToast('服务器地址已保存，正在重新连接喵～', 'success')
    setTimeout(() => window.location.reload(), 800)
  }

  const handleLogout = async () => {
    if (!confirm('确定要退出登录吗？')) return
    setLoggingOut(true)
    try {
      await logoutApi()  // 后端撤销 refresh_token（best-effort）
    } finally {
      authLogout()       // 清 localStorage + dispatch miaoyi:logout
      setLoggingOut(false)
      window.location.reload()
    }
  }

  const handleSaveNickname = async () => {
    const trimmed = nicknameInput.trim()
    if (!trimmed) { onShowToast('昵称不能为空喵～', 'warning'); return }
    if (trimmed.length > 20) { onShowToast('昵称最多 20 个字符喵～', 'warning'); return }
    setSavingNickname(true)
    try {
      await updateNickname(trimmed)
      setUserProfile({ ...(userProfile as any), nickname: trimmed })
      setNickname(trimmed)
      setEditingNickname(false)
      onShowToast('昵称更新成功喵～', 'success')
    } catch (e) {
      onShowToast((e as Error).message || '更新失败喵...', 'error')
    } finally {
      setSavingNickname(false)
    }
  }

  const handleChangePassword = async () => {
    if (!oldPwd || !newPwd || !confirmPwd) { onShowToast('请填写完整喵～', 'warning'); return }
    if (newPwd.length < 8) { onShowToast('新密码至少 8 位喵～', 'warning'); return }
    if (!(/[a-zA-Z]/.test(newPwd) && /[0-9]/.test(newPwd))) { onShowToast('密码需包含字母和数字喵～', 'warning'); return }
    if (newPwd !== confirmPwd) { onShowToast('两次新密码不一致喵～', 'warning'); return }
    if (newPwd === oldPwd) { onShowToast('新密码不能和旧密码相同喵～', 'warning'); return }
    setChangingPassword(true)
    try {
      await updatePassword(oldPwd, newPwd)
      onShowToast('密码修改成功喵～请重新登录', 'success')
      setShowPasswordModal(false)
      setOldPwd(''); setNewPwd(''); setConfirmPwd('')
      setTimeout(() => handleLogout(), 1200)
    } catch (e) {
      onShowToast((e as Error).message || '修改失败喵...', 'error')
    } finally {
      setChangingPassword(false)
    }
  }

  const handleClearProfile = async () => {
    if (!confirm('清除后喵艺将忘记所有关于你的事，确定吗？')) return
    try {
      await clearUserProfile()
      onShowToast('用户画像已清除喵～', 'success')
    } catch (e) {
      onShowToast((e as Error).message || '清除失败喵...', 'error')
    }
  }

  const APP_VERSION = '1.1.9-cloud'

  return (
    <div className="settings-overlay" onClick={onClose}>
      <div className="settings-modal" onClick={(e) => e.stopPropagation()}>
        {/* 顶部标题栏 */}
        <div className="settings-header">
          <button className="settings-back btn-press" onClick={onClose} aria-label="返回"><Icon name="chevronLeft" size={22} /></button>
          <span className="settings-title">设置</span>
          <span className="settings-placeholder" />
        </div>

        <div className="settings-body">
          {/* 账号管理 */}
          <div className="settings-group">
            <div className="settings-group-label">账号</div>
            <div className="settings-group-content">
              {/* 用户头像 + 用户名/昵称概览 */}
              <div className="settings-account-header">
                <div className="settings-account-avatar">
                  {(userProfile?.nickname?.[0] || userProfile?.username?.[0] || <Icon name="cat" size={26} />)}
                </div>
                <div className="settings-account-info">
                  <div className="settings-account-name">
                    {userProfile?.nickname || userProfile?.username || '喵友'}
                  </div>
                  <div className="settings-account-sub">
                    @{userProfile?.username || 'unknown'}
                  </div>
                </div>
              </div>

              {/* 昵称 - 内联编辑 */}
              <div className="settings-row">
                <span className="settings-row-icon"><Icon name="tag" size={17} /></span>
                <span className="settings-row-label">昵称</span>
                {editingNickname ? (
                  <div className="settings-nickname-edit">
                    <input
                      className="settings-nickname-input"
                      value={nicknameInput}
                      onChange={e => setNicknameInput(e.target.value)}
                      maxLength={20}
                      autoFocus
                      onKeyDown={e => e.key === 'Enter' && handleSaveNickname()}
                    />
                    <button
                      className="settings-nickname-btn btn-press"
                      onClick={handleSaveNickname}
                      disabled={savingNickname}
                    >
                      {savingNickname ? '...' : <Icon name="check" size={15} />}
                    </button>
                    <button
                      className="settings-nickname-btn btn-press"
                      onClick={() => setEditingNickname(false)}
                      aria-label="取消"
                    ><Icon name="x" size={15} /></button>
                  </div>
                ) : (
                  <>
                    <span className="settings-row-value">
                      {userProfile?.nickname || '未设置'}
                    </span>
                    <button
                      className="settings-row-arrow btn-press"
                      onClick={() => {
                        setNicknameInput(userProfile?.nickname || '')
                        setEditingNickname(true)
                      }}
                    >›</button>
                  </>
                )}
              </div>

              {/* 用户名（只读） */}
              <div className="settings-row">
                <span className="settings-row-icon"><Icon name="user" size={17} /></span>
                <span className="settings-row-label">用户名</span>
                <span className="settings-row-value">{userProfile?.username || '-'}</span>
              </div>

              {/* 手机号（脱敏显示） */}
              <div className="settings-row">
                <span className="settings-row-icon"><Icon name="smartphone" size={17} /></span>
                <span className="settings-row-label">手机号</span>
                <span className="settings-row-value">{maskPhone(userProfile?.phone)}</span>
              </div>

              {/* 修改密码 - 跳弹窗 */}
              <button className="settings-row btn-press" onClick={() => setShowPasswordModal(true)}>
                <span className="settings-row-icon"><Icon name="key" size={17} /></span>
                <span className="settings-row-label">修改密码</span>
                <span className="settings-row-arrow">›</span>
              </button>

              {/* 清除用户画像 */}
              <button className="settings-row btn-press" onClick={handleClearProfile}>
                <span className="settings-row-icon"><Icon name="trash" size={17} /></span>
                <span className="settings-row-label">清除用户画像</span>
                <span className="settings-row-arrow">›</span>
              </button>
            </div>
          </div>

          {/* 通用 */}
          <div className="settings-group">
            <div className="settings-group-label">通用</div>
            <div className="settings-group-content">
              {/* 主题切换 */}
              <div className="settings-row">
                <span className="settings-row-icon"><Icon name="palette" size={17} /></span>
                <span className="settings-row-label">主题</span>
                <div className="settings-segment">
                  {(['light', 'dark', 'system'] as ThemeMode[]).map((m) => (
                    <button
                      key={m}
                      className={`settings-segment-item btn-press ${theme === m ? 'active' : ''}`}
                      onClick={() => handleThemeChange(m)}
                    >
                      {m === 'light' ? '浅色' : m === 'dark' ? '深色' : '系统'}
                    </button>
                  ))}
                </div>
              </div>
              {/* 字体大小 */}
              <div className="settings-row">
                <span className="settings-row-icon"><Icon name="type" size={17} /></span>
                <span className="settings-row-label">字体大小</span>
                <div className="settings-segment">
                  {(['small', 'medium', 'large'] as FontSize[]).map((s) => (
                    <button
                      key={s}
                      className={`settings-segment-item btn-press ${fontSize === s ? 'active' : ''}`}
                      onClick={() => handleFontSizeChange(s)}
                    >
                      {s === 'small' ? '小' : s === 'medium' ? '中' : '大'}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </div>

          {/* 通知 */}
          <div className="settings-group">
            <div className="settings-group-label">通知与声音</div>
            <div className="settings-group-content">
              <div className="settings-row">
                <span className="settings-row-icon"><Icon name="bell" size={17} /></span>
                <span className="settings-row-label">消息通知</span>
                <span className="settings-row-hint">APK 环境暂不支持</span>
                <button
                  className={`settings-switch btn-press ${notifEnabled ? 'on' : ''}`}
                  onClick={handleToggleNotif}
                  aria-label="切换消息通知"
                  disabled
                  style={{ opacity: 0.4, cursor: 'not-allowed' }}
                />
              </div>
              <div className="settings-row">
                <span className="settings-row-icon"><Icon name="volume" size={17} /></span>
                <span className="settings-row-label">提示音</span>
                <button
                  className={`settings-switch btn-press ${soundEnabled ? 'on' : ''}`}
                  onClick={handleToggleSound}
                  aria-label="切换提示音"
                />
              </div>
              {/* 喵艺声音切换 */}
              <div className="settings-row" onClick={() => setShowVoiceOptions(!showVoiceOptions)}>
                <span className="settings-row-icon"><Icon name="mic" size={17} /></span>
                <span className="settings-row-label">喵艺声音</span>
                <span className="settings-row-value">{voiceModeName}</span>
                <span className="settings-row-arrow">›</span>
              </div>
              {showVoiceOptions && (
                <div className="settings-voice-options">
                  {VOICE_OPTIONS.map(opt => (
                    <button
                      key={opt.mode}
                      className={`settings-voice-option btn-press ${voiceMode === opt.mode ? 'active' : ''}`}
                      onClick={() => handleVoiceChange(opt.mode)}
                    >
                      <span className="settings-voice-name">{opt.name}</span>
                      <span className="settings-voice-desc">{opt.desc}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* 数据 */}
          <div className="settings-group">
            <div className="settings-group-label">数据管理</div>
            <div className="settings-group-content">
              <button className="settings-row btn-press" onClick={handleClearCache}>
                <span className="settings-row-icon"><Icon name="trash" size={17} /></span>
                <span className="settings-row-label">清除缓存</span>
                <span className="settings-row-arrow">›</span>
              </button>
              <button className="settings-row btn-press" onClick={handleClearConversations}>
                <span className="settings-row-icon"><Icon name="messageSquare" size={17} /></span>
                <span className="settings-row-label">清除聊天记录</span>
                <span className="settings-row-arrow">›</span>
              </button>
              <button className="settings-row btn-press" onClick={handleOpenServerModal}>
                <span className="settings-row-icon"><Icon name="globe" size={17} /></span>
                <span className="settings-row-label">服务器地址</span>
                <span className="settings-row-value">{getBaseUrl() === '/api' ? '默认' : getBaseUrl()}</span>
                <span className="settings-row-arrow">›</span>
              </button>
            </div>
          </div>

          {/* 关于 */}
          <div className="settings-group">
            <div className="settings-group-label">关于</div>
            <div className="settings-group-content">
              <button className="settings-row btn-press" onClick={() => setShowAbout(true)}>
                <span className="settings-row-icon"><Icon name="info" size={17} /></span>
                <span className="settings-row-label">关于喵艺</span>
                <span className="settings-row-arrow">›</span>
              </button>
              <a className="settings-row btn-press" href="/privacy" target="_blank" rel="noopener">
                <span className="settings-row-icon"><Icon name="clipboard" size={17} /></span>
                <span className="settings-row-label">隐私政策</span>
                <span className="settings-row-arrow">›</span>
              </a>
              <a className="settings-row btn-press" href="/terms" target="_blank" rel="noopener">
                <span className="settings-row-icon"><Icon name="fileText" size={17} /></span>
                <span className="settings-row-label">用户协议</span>
                <span className="settings-row-arrow">›</span>
              </a>
              <div className="settings-row">
                <span className="settings-row-icon"><Icon name="tag" size={17} /></span>
                <span className="settings-row-label">版本</span>
                <span className="settings-row-value">v{APP_VERSION}</span>
              </div>
            </div>
          </div>

          {/* 退出登录 */}
          <div className="settings-group">
            <div className="settings-group-content">
              <button className="settings-logout btn-press" onClick={handleLogout} disabled={loggingOut}>
                {loggingOut ? '退出中...' : '退出登录'}
              </button>
            </div>
          </div>

          <div className="settings-footer">
            <span><Icon name="cat" size={15} /> 喵艺 AI 伙伴 · 让陪伴更有温度</span>
          </div>
        </div>

        {/* 服务器地址弹窗 */}
        {showServerModal && (
          <div className="settings-about-overlay" onClick={() => setShowServerModal(false)}>
            <div className="settings-about" onClick={(e) => e.stopPropagation()}>
              <h2 className="settings-about-name"><Icon name="globe" size={18} /> 服务器地址</h2>
              <p className="settings-about-desc">
                手机 App 无法使用相对路径连接后端，请填写电脑/服务器的地址（如 http://192.168.1.100:8001）。留空则使用默认地址。
              </p>
              <input
                type="text"
                className="settings-nickname-input"
                placeholder="http://192.168.x.x:8001"
                value={serverUrlInput}
                onChange={e => setServerUrlInput(e.target.value)}
              />
              <div className="settings-about-actions">
                <button className="settings-about-close btn-press" onClick={handleSaveServerUrl}>保存</button>
                <button className="settings-about-close btn-press" onClick={() => setShowServerModal(false)}>取消</button>
              </div>
            </div>
          </div>
        )}

        {/* 修改密码弹窗 */}
        {showPasswordModal && (
          <div className="settings-about-overlay" onClick={() => setShowPasswordModal(false)}>
            <div className="settings-about" onClick={(e) => e.stopPropagation()}>
              <h2 className="settings-about-name"><Icon name="key" size={18} /> 修改密码</h2>
              <p className="settings-about-desc">修改后其他设备需重新登录喵～</p>
              <input
                type="password"
                className="settings-nickname-input"
                placeholder="原密码"
                value={oldPwd}
                onChange={e => setOldPwd(e.target.value)}
              />
              <input
                type="password"
                className="settings-nickname-input"
                placeholder="新密码（至少8位，含字母和数字）"
                value={newPwd}
                onChange={e => setNewPwd(e.target.value)}
              />
              <input
                type="password"
                className="settings-nickname-input"
                placeholder="确认新密码"
                value={confirmPwd}
                onChange={e => setConfirmPwd(e.target.value)}
              />
              <div className="settings-about-actions">
                <button
                  className="settings-about-close btn-press"
                  onClick={handleChangePassword}
                  disabled={changingPassword}
                >
                  {changingPassword ? '修改中...' : '确认修改'}
                </button>
                <button
                  className="settings-about-close btn-press"
                  onClick={() => setShowPasswordModal(false)}
                >取消</button>
              </div>
            </div>
          </div>
        )}

        {/* 关于弹窗 */}
        {showAbout && (
          <div className="settings-about-overlay" onClick={() => setShowAbout(false)}>
            <div className="settings-about" onClick={(e) => e.stopPropagation()}>
              <div className="settings-about-icon"><Icon name="cat" size={40} /></div>
              <h2 className="settings-about-name">喵艺</h2>
              <p className="settings-about-version">v{APP_VERSION}</p>
              <p className="settings-about-desc">
                喵艺是一款AI情感陪伴应用，以傲娇猫娘人设提供聊天陪伴、语音通话和情绪互动。
                她会记住你说过的话，感受你的情绪，在你需要的时候陪伴你。
              </p>
              <p className="settings-about-notice">本应用由AI生成内容，已进行内容安全审核。</p>
              <button className="settings-about-close btn-press" onClick={() => setShowAbout(false)}>
                知道了
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
