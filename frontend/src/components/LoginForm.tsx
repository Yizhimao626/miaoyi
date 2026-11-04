import { useEffect, useState } from 'react'
import { login, register, getAuthConfig, getBaseUrl, setBaseUrl, resetBaseUrl } from '../utils/api'
import { setUsername } from '../utils/storage'
import { setTokens } from '../utils/auth'
import { Icon } from './icons'

interface LoginFormProps {
  onLogin: (accessToken?: string, refreshToken?: string) => void
  onShowToast: (message: string, type?: 'success' | 'error' | 'warning') => void
}

type FormMode = 'login' | 'register'

function validatePhone(phone: string): boolean {
  return /^1[3-9]\d{9}$/.test(phone)
}

function checkPasswordStrength(password: string): { level: number; message: string; color: string } {
  let level = 0
  if (password.length >= 8) level++
  if (/[a-zA-Z]/.test(password)) level++
  if (/[0-9]/.test(password)) level++
  
  const messages = ['', '密码太弱', '密码强度中等', '密码强度强']
  const colors = ['', '#ff4757', '#ffa502', '#2ed573']
  return { level, message: messages[level] || '', color: colors[level] || '' }
}

export function LoginForm({ onLogin, onShowToast }: LoginFormProps) {
  const [mode, setMode] = useState<FormMode>('login')
  const [username, setUsernameState] = useState('')
  const [phone, setPhone] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [inviteCode, setInviteCode] = useState('')
  const [error, setError] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [showPrivacyModal, setShowPrivacyModal] = useState(false)
  const [focusedField, setFocusedField] = useState<string | null>(null)
  const [inviteRequired, setInviteRequired] = useState(false)
  const [showServerCfg, setShowServerCfg] = useState(false)
  const [serverInput, setServerInput] = useState('')

  const passwordStrength = checkPasswordStrength(password)

  // 拉取登录页公开配置：是否强制邀请码（失败默认不要求，避免卡住注册）
  useEffect(() => {
    getAuthConfig()
      .then((res) => setInviteRequired(!!res.data?.invite_code_required))
      .catch(() => { /* 静默：按开放注册处理 */ })
  }, [])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')

    if (!username.trim()) {
      setError('喵～你还没告诉我叫什么名字呢')
      return
    }

    if (!password.trim()) {
      setError('喵～密码是空的哦，本喵会守不住门的')
      return
    }

    if (mode === 'register') {
      if (!phone.trim()) {
        setError('喵～留个手机号吧，万一走丢了好找回来')
        return
      }
      if (!validatePhone(phone)) {
        setError('*手机号格式不对喵～应该是11位数字哦')
        return
      }
      if (password.length < 6) {
        setError('*密码太短了喵，至少6位才安全~')
        return
      }
      if (password.length < 8) {
        setError('密码有点短喵，建议至少8位更安全哦')
        return
      }
      if (!(/[a-zA-Z]/.test(password) && /[0-9]/.test(password))) {
        setError('密码最好包含字母和数字喵～更难猜')
        return
      }
      if (password !== confirmPassword) {
        setError('喵～两次输入的密码不一样呢，再检查下')
        return
      }
    }

    setIsLoading(true)

    try {
      if (mode === 'login') {
        const result = await login(username, password)
        const accessToken = result.data.access_token || result.data.token
        const refreshToken = result.data.refresh_token
        
        // 使用新的 auth 模块保存双令牌
        setTokens(accessToken, refreshToken)
        setUsername(username)
        onShowToast('登录成功喵～欢迎回来铲屎官！', 'success')
        onLogin(accessToken, refreshToken)
      } else {
        const result = await register(username.trim(), phone, password, inviteCode.trim())
        const regData = result.data
        const regAccess = regData.access_token || regData.token
        const regRefresh = regData.refresh_token
        // 注册接口已签发令牌：直接登录，省去再次输入密码（减少“注册了却登不进”）
        setTokens(regAccess, regRefresh)
        setUsername(regData.username || username)
        onShowToast('注册成功喵～欢迎来到喵艺，以后请多指教！', 'success')
        onLogin(regAccess, regRefresh)
      }
    } catch (err) {
      const errorMsg = (err as Error).message
      // 后端返回的错误也转换为猫语；锁定/禁用/邀请码等关键信息原样透传
      if (errorMsg.includes('已存在') || errorMsg.includes('已经被使用') || errorMsg.includes('exist')) {
        setError('喵～这个用户名/手机号已经有主人了呢，换一个试试吧')
      } else if (errorMsg.includes('锁定')) {
        setError(errorMsg)
      } else if (errorMsg.includes('禁用')) {
        setError(errorMsg)
      } else if (errorMsg.includes('邀请码')) {
        setError(errorMsg)
      } else if (errorMsg.includes('密码错误') || errorMsg.includes('用户名或密码错误') || errorMsg.includes('incorrect')) {
        setError('喵～用户名或密码不对哦，再想想看？')
      } else if (errorMsg.includes('用户不存在') || errorMsg.includes('not found')) {
        setError('喵～找不到这个账号呢，是不是还没注册？')
      } else {
        setError(errorMsg || '操作失败喵...请稍后再试')
      }
    } finally {
      setIsLoading(false)
    }
  }

  const inputFields = [
    { name: 'username', label: mode === 'login' ? '用户名或手机号' : '用户名', type: 'text', value: username, onChange: setUsernameState, placeholder: mode === 'login' ? '请输入用户名或手机号' : '给自己取个名字吧' },
    ...(mode === 'register' ? [{ name: 'phone', label: '手机号', type: 'tel', value: phone, onChange: (v: string) => setPhone(v.replace(/\D/g, '').slice(0, 11)), placeholder: '11位手机号' }] : []),
    { name: 'password', label: '密码', type: 'password', value: password, onChange: setPassword, placeholder: mode === 'login' ? '请输入密码' : '至少6位，包含字母和数字' },
    ...(mode === 'register' && inviteRequired ? [{ name: 'invite', label: '邀请码', type: 'text', value: inviteCode, onChange: (v: string) => setInviteCode(v), placeholder: '请输入邀请码' }] : []),
    ...(mode === 'register' ? [{ name: 'confirm', label: '确认密码', type: 'password', value: confirmPassword, onChange: setConfirmPassword, placeholder: '再输一遍密码' }] : []),
  ]

  {/* 字段级错误提示已由错误气泡显示 */}

  // 获取应该显示错误的字段
  const getErrorField = (): string | null => {
    if (!error) return null
    if (error.includes('手机号') || error.includes('11位')) return 'phone'
    if (error.includes('密码') || error.includes('安全')) return 'password'
    if (error.includes('两次')) return 'confirm'
    if (error.includes('名字') || error.includes('叫什么')) return 'username'
    return null
  }

  return (
    <div className="login-screen">
      <div className="login-header">
        <div className="login-logo-wrapper">
          <div className="login-logo"><Icon name="cat" size={56} /></div>
          <div className="login-logo-ring" />
        </div>
        <div className="login-title">喵艺</div>
        <div className="login-subtitle">有灵魂的AI聊天伙伴</div>
      </div>

      <div className="login-tabs">
        <button
          className={`login-tab btn-press ${mode === 'login' ? 'active' : ''}`}
          onClick={() => setMode('login')}
        >
          <span className="login-tab-icon"><Icon name="lock" size={14} /></span>
          登录
        </button>
        <button
          className={`login-tab btn-press ${mode === 'register' ? 'active' : ''}`}
          onClick={() => setMode('register')}
        >
          <span className="login-tab-icon"><Icon name="sparkles" size={14} /></span>
          注册
        </button>
      </div>

      <form className="login-form" onSubmit={handleSubmit}>
        {inputFields.map((field) => {
          const hasError = getErrorField() === field.name
          return (
            <div key={field.name} className="login-input-group">
              <div className={`login-input-wrapper ${focusedField === field.name ? 'focused' : ''} ${hasError ? 'error' : ''}`}>
                <span className="login-input-icon">
                  <Icon
                    name={field.name === 'username' ? 'user' : field.name === 'phone' ? 'smartphone' : field.name === 'confirm' ? 'lock' : 'key'}
                    size={18}
                  />
                </span>
                <input
                  type={field.type}
                  className={`login-input ${hasError ? 'input-error' : ''}`}
                  placeholder={field.placeholder || field.label}
                  value={field.value}
                  onChange={(e) => field.onChange(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleSubmit(e)}
                  onFocus={() => setFocusedField(field.name)}
                  onBlur={() => setFocusedField(null)}
                  autoComplete={field.type === 'password' ? 'current-password' : field.name}
                />
                {field.name === 'phone' && field.value.length === 11 && validatePhone(field.value) && (
                  <span className="login-input-valid"><Icon name="check" size={16} /></span>
                )}
                {hasError && (
                  <span className="input-error-icon"><Icon name="alert" size={16} /></span>
                )}
              </div>
              {/* 字段级错误提示 - 猫语气泡 */}
              {hasError && error && (
                <div className="field-error-bubble">
                  <span className="bubble-cat"><Icon name="cat" size={20} /></span>
                  <span className="bubble-text">{error}</span>
                  <span className="bubble-arrow" />
                </div>
              )}
              {/* 密码强度提示 */}
              {field.name === 'phone' && !hasError && field.value && !validatePhone(field.value) && (
                <div className="field-hint">喵～应该是11位手机号哦</div>
              )}
              {field.name === 'password' && mode === 'register' && !hasError && field.value && passwordStrength.level < 2 && (
                <div className="field-hint">密码最好包含字母和数字喵～</div>
              )}
            </div>
          )
        })}
        
        {mode === 'register' && password && (
          <div className="password-strength">
            <div className="password-strength-label"><Icon name="shield" size={14} /> 密码安全度</div>
            <div className="password-strength-bars">
              {[1, 2, 3].map((l) => (
                <div
                  key={l}
                  className={`password-strength-bar ${l <= passwordStrength.level ? 'active' : ''}`}
                  style={{ backgroundColor: l <= passwordStrength.level ? passwordStrength.color : '#ddd' }}
                />
              ))}
            </div>
            <span className="password-strength-text" style={{ color: passwordStrength.color }}>
              {passwordStrength.level === 0 && '太简单了喵～'}
              {passwordStrength.level === 1 && '还可以喵～'}
              {passwordStrength.level === 2 && '不错哦喵！'}
              {passwordStrength.level === 3 && '非常安全喵！'}
            </span>
          </div>
        )}
        
        {/* 加载状态显示猫语 */}
        <button type="submit" className="login-btn btn-press" disabled={isLoading}>
          {isLoading ? (
            <>
              <span className="login-btn-spinner" />
              <span className="loading-cat">喵爪计算中...</span>
            </>
          ) : (
            <>
              <span className="login-btn-icon"><Icon name="paw" size={18} /></span>
              {mode === 'login' ? '带我回家喵～' : '创建账号喵～'}
            </>
          )}
        </button>
        
        {mode === 'register' && (
          <div className="login-privacy">
            <label className="privacy-checkbox">
              <input type="checkbox" defaultChecked />
              <span>我已阅读并同意</span>
              <button type="button" className="privacy-link" onClick={() => setShowPrivacyModal(true)}>
                隐私政策
              </button>
            </label>
          </div>
        )}
      </form>

      {/* 服务器连接设置：默认地址连不上时（旧版残留/特殊网络），用户可在此自救 */}
      <div className="login-server-cfg">
        <button
          type="button"
          className="login-server-cfg-toggle btn-press"
          onClick={() => {
            if (!showServerCfg) setServerInput(getBaseUrl())
            setShowServerCfg(!showServerCfg)
          }}
        >
          <Icon name="globe" size={14} /> 连不上？服务器连接设置
        </button>
        {showServerCfg && (
          <div className="login-server-cfg-panel">
            <input
              type="text"
              className="login-server-cfg-input"
              value={serverInput}
              onChange={(e) => setServerInput(e.target.value)}
              placeholder="如 http://服务器IP:8001/api"
            />
            <button
              type="button"
              className="login-server-cfg-save btn-press"
              onClick={() => {
                const v = serverInput.trim().replace(/\/+$/, '')
                if (!/^https?:\/\//.test(v)) {
                  onShowToast('地址要以 http:// 或 https:// 开头喵～', 'warning')
                  return
                }
                // 【P1修复】完整 URL 校验：挡住 "http://" 空壳等无效输入
                let final = v
                try {
                  const u = new URL(v)
                  if (!u.hostname || !u.hostname.includes('.')) {
                    throw new Error('invalid-host')
                  }
                  // REST 挂载在 /api/*：用户填裸地址（无路径）时自动补 /api，
                  // 避免照 "http://地址:端口" 直觉填写后所有请求 404
                  if (u.pathname === '/' || u.pathname === '') final = `${v}/api`
                } catch {
                  onShowToast('这不是有效的服务器地址喵～检查下 IP 有没有写对', 'warning')
                  return
                }
                setBaseUrl(final)
                setShowServerCfg(false)
                onShowToast('服务器地址已保存喵～再试试登录吧', 'success')
              }}
            >
              保存地址
            </button>
            <button
              type="button"
              className="login-server-cfg-save btn-press"
              onClick={() => {
                // 【P1修复】填错地址后无路可退的问题：一键回退到构建时默认地址
                resetBaseUrl()
                setServerInput(getBaseUrl())
                setShowServerCfg(false)
                onShowToast('已恢复默认服务器地址喵～', 'success')
              }}
            >
              恢复默认
            </button>
            <p className="login-server-cfg-hint">云服务器需带 /api 后缀（填裸地址会自动补全）；留空恢复默认即可</p>
          </div>
        )}
      </div>

      {showPrivacyModal && (
        <div className="privacy-modal-overlay" onClick={() => setShowPrivacyModal(false)}>
          <div className="privacy-modal" onClick={(e) => e.stopPropagation()}>
            <div className="privacy-modal-header">
              <h3>隐私政策</h3>
              <button className="privacy-modal-close" onClick={() => setShowPrivacyModal(false)}>
                <Icon name="x" size={18} />
              </button>
            </div>
            <div className="privacy-modal-content">
              <p>我们非常重视您的隐私安全。</p>
              <h4>1. 收集的信息</h4>
              <p>- 用户名和密码：用于账户登录和身份验证</p>
              <p>- 手机号：用于账号安全验证和找回密码</p>
              <p>- 聊天记录：用于提供个性化服务和记忆功能</p>
              <h4>2. 使用方式</h4>
              <p>- 您的个人信息仅用于提供和改进我们的服务</p>
              <p>- 我们不会出售或分享您的个人信息给第三方</p>
              <h4>3. 数据安全</h4>
              <p>- 您的密码经过加密存储</p>
              <p>- 所有数据传输采用安全协议</p>
            </div>
            <button className="privacy-modal-accept" onClick={() => setShowPrivacyModal(false)}>
              我已知晓
            </button>
          </div>
        </div>
      )}
    </div>
  )
}