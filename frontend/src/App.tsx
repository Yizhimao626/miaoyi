import { useEffect, useCallback, useState, useRef, lazy, Suspense } from 'react'
import { SplashScreen } from './components/SplashScreen'
import { LoginForm } from './components/LoginForm'
import { ChatView } from './components/ChatView'
import { Sidebar } from './components/Sidebar'
import { SessionList } from './components/SessionList'
import { Toast } from './components/Toast'
import { PrivacyModal } from './components/PrivacyModal'
import { ScreenTimeReminder } from './components/ScreenTimeReminder'
import Skeleton from './components/Skeleton'
// 【P2 懒加载】低频重组件按需分包，减小主 bundle
const AdminPanel = lazy(() => import('./components/AdminPanel').then((m) => ({ default: m.AdminPanel })))
import { PerformanceMonitor } from './components/PerformanceMonitor'
import { OnboardingGuide } from './components/OnboardingGuide'
const SettingsModal = lazy(() => import('./components/SettingsModal').then((m) => ({ default: m.SettingsModal })))
import { Onboarding } from './components/Onboarding'
// 【性能优化】页面级懒加载：非当前 tab 的页面不进入首屏 bundle
const ProfilePage = lazy(() => import('./components/ProfilePage').then((m) => ({ default: m.ProfilePage })))
import { BottomNav } from './components/BottomNav'
const HomePage = lazy(() => import('./pages/HomePage').then((m) => ({ default: m.HomePage })))
const MemoryPage = lazy(() => import('./pages/MemoryPage').then((m) => ({ default: m.MemoryPage })))
const MiaoyiPage = lazy(() => import('./pages/MiaoyiPage').then((m) => ({ default: m.MiaoyiPage })))
import { useWebSocket } from './hooks/useWebSocket'
import { useNetwork } from './hooks/useNetwork'
import { useKeyboard } from './hooks/useKeyboard'
import { useBackButton } from './hooks/useBackButton'
import { Capacitor } from '@capacitor/core'
import { App as CapApp } from '@capacitor/app'
import { StatusBar, Style } from '@capacitor/status-bar'
import { UpdateDialog } from './components/UpdateDialog'
import { useLocalCache } from './hooks/useLocalCache'
import { CallScreen } from './components/CallScreen'
import { getTheme, applyTheme, getPrivacyAgreed, setPrivacyAgreed, subscribeToSystemTheme, unsubscribeFromSystemTheme, getMinorMode } from './utils/storage'
import { getProfile, getOnboarding, getDailyFragment, checkin, checkAppUpdate } from './utils/api'
import type { AppVersionInfo } from './utils/api'
import { checkAuthStatus, setTokens as setAuthTokens, clearTokens as clearAuthTokens } from './utils/auth'
import { dbManager } from './utils/db'
import type { Session } from './types'
import { useChatStore } from './store/chatStore'
import { useUserStore } from './store/userStore'
import { useUIStore } from './store/uiStore'
import './App.css'
import './styles/ui-upgrade.css'
import './styles/dreamlight.css' // 【方案一】梦幻浅色皮肤，最后加载，一票否决
import './styles/miaoyi-design.css' // 【官方版图】设计落地最终层，覆盖上方所有皮肤

interface AppProps {
  onReady?: () => void
}

function App({ onReady }: AppProps) {
  const { sendMessage, reconnect, retryMessage } = useWebSocket()
  const { isOnline } = useNetwork()
  // 全局键盘遮挡适配：测量真实未覆盖差值并写入 --kb-inset（全 App 输入框受益）
  useKeyboard()

  const { getMessages, setMessages, initSessionMessages, currentSessionId, setCurrentSessionId, connectionStatus, emotionalState, growthData, isTyping, thinkingStatus, thinkingStep, thinkingProgress, thinkingStepIndex, thinkingTotalSteps, failedMessageIds, retryAllFailed, markSessionAsRead, messagesBySession, resetStore } = useChatStore()

  const { isLoggedIn, isAdmin, currentSession, setIsLoggedIn, setIsAdmin, setCurrentSession } = useUserStore()

  const { view, isSidebarOpen, toast, showPrivacyModal, showSessionList, showAdminPanel, setView, setIsSidebarOpen, showToast, hideToast, setShowPrivacyModal, setShowSessionList, toggleSidebar, toggleSessionList, setShowAdminPanel } = useUIStore()

  const [onboardingData, setOnboardingData] = useState<{
    is_first_time: boolean
    greeting: string
    options: Array<{ id: string; title: string; description: string; trigger_text: string }>
  } | null>(null)
  const [dailyFragment, setDailyFragment] = useState<string | null>(null)
  // 支持 ?tab= 深链接直达指定页（默认首页）
  const [currentTab, setCurrentTab] = useState<'home' | 'chat' | 'memory' | 'miaoyi' | 'profile'>(() => {
    const t = new URLSearchParams(window.location.search).get('tab')
    return (['home', 'chat', 'memory', 'miaoyi', 'profile'] as const).includes(t as any) ? (t as any) : 'home'
  })
  const [dailyCheckin, setDailyCheckin] = useState(false)
  const [showGuide, setShowGuide] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [openServerModal, setOpenServerModal] = useState(false)
  const [showLoginTransition, setShowLoginTransition] = useState(false)
  const [showWelcomeText, setShowWelcomeText] = useState(false)

  // 【App 更新】检查到新版本时的弹窗数据
  const [updateInfo, setUpdateInfo] = useState<AppVersionInfo | null>(null)

  // 【合规】连续使用 2 小时强制休息提醒（2026-07-15 新规）
  const [screenTimeMinutes, setScreenTimeMinutes] = useState(0)
  const [showScreenTimeReminder, setShowScreenTimeReminder] = useState(false)
  // 【合规】未成年人模式（设置页可开关，开启后顶部常驻提示）
  const [minorMode, setMinorModeState] = useState(getMinorMode())

  useEffect(() => {
    const handler = (e: Event) => {
      setMinorModeState((e as CustomEvent<boolean>).detail)
    }
    window.addEventListener('miaoyi_minor_mode_changed', handler)
    return () => window.removeEventListener('miaoyi_minor_mode_changed', handler)
  }, [])

  const { saveCache } = useLocalCache(currentSessionId)

  // 【P1修复·隐私】统一的用户数据清理：手动登出 / token 失效强制登出 / WS 认证失败
  // 三条路径必须走同一个清理，否则换账号登录能看到上一任用户的聊天记录与记忆。
  const cleanupUserData = useCallback(() => {
    // 【递归修复】这里只清 token，不能调用 auth.logout()：
    // 后者会派发 miaoyi:logout 事件，而本文件的监听器又会回调 cleanupUserData，
    // 导致 Maximum call stack size exceeded（登出 / token 失效双双栈溢出）
    clearAuthTokens()
    resetStore()
    try {
      const keys = [
        'miaoyi_messages_cache',
        'miaoyi_ws_message_queue',
        'miaoyi_offline_queue',
        'miaoyi_memories',
        'miaoyi_pinned_sessions',
        'miaoyi_username',
        'miaoyi_nickname',
        'achievements',
        'petCount',
        'checkinStreak',
      ]
      keys.forEach((k) => localStorage.removeItem(k))
    } catch {
      /* ignore */
    }
    void dbManager.clearAll()
  }, [resetStore])

  // 【移动适配】Android 物理返回键拦截：浮层优先关闭、其他 Tab 回聊天、聊天页退后台
  useBackButton({
    active: view === 'main' && isLoggedIn,
    currentTab,
    setCurrentTab,
    showSettings,
    setShowSettings,
  })

  // 【移动适配】原生状态栏：品牌为浅色背景，需要深色图标。
  // Style.Light 在该插件语义中是"深色文字用于浅色背景"（命名与直觉相反）
  useEffect(() => {
    if (!Capacitor.isNativePlatform()) return
    StatusBar.setStyle({ style: Style.Light }).catch(() => {})
    StatusBar.setOverlaysWebView({ overlay: false }).catch(() => {})
  }, [])

  // 检查是否显示新手引导
  useEffect(() => {
    if (view === 'main' && isLoggedIn) {
      const hasShown = localStorage.getItem('miaoyi_onboarding_shown')
      if (!hasShown) {
        // 延迟显示，等待主界面渲染完成
        const timer = setTimeout(() => setShowGuide(true), 1500)
        return () => clearTimeout(timer)
      }
    }
  }, [view, isLoggedIn])

  // 【App 更新】进入主界面后检查一次（仅 Android 原生平台）
  useEffect(() => {
    if (view !== 'main' || !isLoggedIn) return
    if (Capacitor.getPlatform() !== 'android') return
    let cancelled = false
    const timer = setTimeout(async () => {
      try {
        const info = await CapApp.getInfo()
        const versionCode = parseInt(info.build || '0', 10) || 0
        const data = await checkAppUpdate('android', versionCode)
        if (cancelled || !data || !data.has_update) return
        // 非强制更新：用户已选择跳过该版本，则不再打扰
        if (!data.force_update
          && localStorage.getItem('miaoyi_skipped_update') === String(data.latest_version_code)) return
        setUpdateInfo(data)
      } catch {
        // 更新检查失败保持静默
      }
    }, 2000)
    return () => { cancelled = true; clearTimeout(timer) }
  }, [view, isLoggedIn])

  useEffect(() => {
    applyTheme(getTheme())
    subscribeToSystemTheme()

    return () => {
      unsubscribeFromSystemTheme()
    }
  }, [])

  useEffect(() => {
    const currentMessages = getMessages(currentSessionId)
    
    if (currentMessages.length > 0) {
      if (onReady) {
        requestAnimationFrame(() => {
          requestAnimationFrame(onReady)
        })
      }
      return
    }

    const timeout = setTimeout(() => {
      if (onReady) {
        requestAnimationFrame(onReady)
      }
    }, 3000)

    return () => clearTimeout(timeout)
  }, [getMessages, currentSessionId, onReady])

  useEffect(() => {
    const auth = checkAuthStatus()
    if (auth.isAuthenticated) {
      setIsLoggedIn(true)
      // 如果 token 即将过期，让 api.ts 的拦截器自动刷新
      checkAdminRole()
      loadLocalMessages()
      fetchOnboardingData()
      fetchDailyFragment()
    }

    // 监听强制登出事件
    const handleRequireLogin = () => {
      // 【P1修复·隐私】token 刷新失败的强制登出也要清理本地数据，
      // 与手动登出同路径：换账号登录不能看到上一任用户的聊天记录
      cleanupUserData()
      setIsLoggedIn(false)
      setIsAdmin(false)
      setView('login')
    }
    // 页面间跳转事件（我的页 → 记忆页等）
    const handleGoTab = (e: Event) => {
      const detail = (e as CustomEvent).detail
      if (detail === 'memory' || detail === 'home' || detail === 'chat' || detail === 'miaoyi' || detail === 'profile') {
        setCurrentTab(detail)
      }
    }
    window.addEventListener('miaoyi:require-login', handleRequireLogin)
    window.addEventListener('miaoyi:logout', handleRequireLogin)
    window.addEventListener('miaoyi:go-tab', handleGoTab)
    return () => {
      window.removeEventListener('miaoyi:require-login', handleRequireLogin)
      window.removeEventListener('miaoyi:logout', handleRequireLogin)
      window.removeEventListener('miaoyi:go-tab', handleGoTab)
    }
  }, [cleanupUserData])

  const fetchOnboardingData = async () => {
    try {
      const result = await getOnboarding()
      if (result.data.is_first_time) {
        setOnboardingData(result.data)
      }
    } catch (error) {
      console.error('获取onboarding数据失败', error)
    }
  }

  const fetchDailyFragment = async () => {
    try {
      const result = await getDailyFragment()
      setDailyFragment(result.data.fragment)
    } catch (error) {
      console.error('获取每日碎片失败', error)
    }
  }

  const loadLocalMessages = async () => {
    try {
      const sessions = await dbManager.getSessions()
      if (sessions.length > 0) {
        const activeSession = sessions[0]
        setCurrentSession(activeSession)
        setCurrentSessionId(activeSession.id)
        const messages = await dbManager.getMessages(activeSession.id)
        initSessionMessages(activeSession.id, messages)
      } else {
        const defaultSession: Session = {
          id: 'default',
          name: '喵艺',
          lastMessage: '',
          lastTime: Date.now(),
          unreadCount: 0,
        }
        await dbManager.saveSession(defaultSession)
        setCurrentSession(defaultSession)
        setCurrentSessionId('default')
      }
    } catch (error) {
      console.error('加载本地消息失败', error)
      const defaultSession: Session = {
        id: 'default',
        name: '喵艺',
        lastMessage: '',
        lastTime: Date.now(),
        unreadCount: 0,
      }
      setCurrentSession(defaultSession)
      setCurrentSessionId('default')
    }
  }

  useEffect(() => {
    const currentMessages = getMessages(currentSessionId)
    if (currentMessages.length > 0) {
      saveCache(currentMessages)
    }
  }, [getMessages, currentSessionId, saveCache])

  // 【性能优化】已持久化的消息 id 集合：只增量写入新增/变更的消息，
  // 避免每次消息变化都全量重写 IndexedDB（长会话下卡顿）。
  const persistedMsgIdsRef = useRef<Set<string>>(new Set())
  const persistingRef = useRef(false)

  // 会话切换时重置持久化追踪（不同会话的 id 不通用）
  useEffect(() => {
    persistedMsgIdsRef.current = new Set()
  }, [currentSessionId])

  useEffect(() => {
    if (persistingRef.current) return
    const currentMessages = getMessages(currentSessionId)
    if (currentMessages.length === 0) return

    // 找出尚未持久化的消息（含状态从 sending→done 的变更：同 id 也重存）
    const toPersist = currentMessages.filter((m) => !persistedMsgIdsRef.current.has(m.id))
    if (toPersist.length === 0) return

    persistingRef.current = true
    const saveMessages = async () => {
      try {
        await dbManager.addMessages(toPersist, currentSessionId)
        toPersist.forEach((m) => persistedMsgIdsRef.current.add(m.id))
        if (currentSession) {
          const lastMsg = currentMessages[currentMessages.length - 1]
          await dbManager.saveSession({
            ...currentSession,
            lastMessage: lastMsg.text.substring(0, 50),
            lastTime: lastMsg.timestamp,
          })
        }
      } catch (e) {
        console.warn('[DB] 消息持久化失败（下次变更重试）:', e)
        // 失败时移除标记，允许下次重试
        toPersist.forEach((m) => persistedMsgIdsRef.current.delete(m.id))
      } finally {
        persistingRef.current = false
      }
    }
    saveMessages()
  }, [getMessages, currentSessionId, currentSession])

  const checkAdminRole = async () => {
    try {
      const result = await getProfile()
      setIsAdmin(result.data.role === 'admin' || result.data.role === 'founder')
    } catch (error) {
      console.error('检查管理员角色失败', error)
    }
  }

  useEffect(() => {
    if (!isOnline) {
      showToast('喵～网络好像断了，检查下网络连接哦~', 'error')
    } else if (connectionStatus === 'disconnected' && isLoggedIn) {
      // 【风暴修复 20260930】仅登录状态下才自动重连——未登录时 disconnected 是合法状态，
      // 旧版无条件 reconnect() 与 useWebSocket 的 token 检查形成 connecting/disconnected 无限循环
      // 【断线风暴修复】这里只做提示、不再立即 reconnect()：useWebSocket 的 onclose
      // 已带指数退避自动重连；App 层立即重连会与状态交替叠加成紧循环握手风暴
      showToast('喵～正在努力重新连接中...', 'warning')
    } else if (connectionStatus === 'auth_failed') {
      showToast('喵～登录过期了，重新登录一下吧~', 'error')
      // 【P1修复·隐私】认证失败同走统一清理，防止换号看到前任数据
      cleanupUserData()
      setIsLoggedIn(false)
      setIsAdmin(false)
      setView('login')
    }
  }, [isOnline, connectionStatus, isLoggedIn, reconnect, showToast, setIsLoggedIn, setIsAdmin, setView, cleanupUserData])

  // 【#3 修复】删除 App 层重复的 visibilitychange 监听：useWebSocket 内部已有
  // 带 !isConnectedRef 守卫的同功能监听。旧版这个监听只判断 token 就 reconnect()，
  // 会把切出切回时完全健康的连接强制关闭重建，打断正在进行的 AI 回复。

  const handleSplashComplete = useCallback(() => {
    const hasAgreed = getPrivacyAgreed()
    const auth = checkAuthStatus()

    if (!hasAgreed) {
      setShowPrivacyModal(true)
    } else if (auth.isAuthenticated) {
      setView('main')
    } else {
      setView('login')
    }
  }, [setShowPrivacyModal, setView])

  const handleLogin = useCallback((accessToken?: string, refreshToken?: string) => {
    if (accessToken) {
      setAuthTokens(accessToken, refreshToken)
    }
    setIsLoggedIn(true)
    
    // 显示登录过渡动画
    setShowLoginTransition(true)
    setShowWelcomeText(false)
    
    // 猫爪印扩散动画
    setTimeout(() => {
      setShowWelcomeText(true)
    }, 300)
    
    // 过渡完成后切换到主界面
    setTimeout(() => {
      setView('main')
      setShowLoginTransition(false)
      setShowWelcomeText(false)
      checkAdminRole()
      reconnect()
    }, 1500)
  }, [setIsLoggedIn, setView, reconnect])

  const handleLogout = useCallback(() => {
    // 【P1修复·隐私】走统一清理（含 #6 修复的 IndexedDB + 扩展的 localStorage 键）
    cleanupUserData()
    setIsLoggedIn(false)
    setIsAdmin(false)
    setView('login')
  }, [cleanupUserData, setIsLoggedIn, setIsAdmin, setView])

  const handlePrivacyAgree = useCallback(() => {
    setPrivacyAgreed(true)
    setShowPrivacyModal(false)
    const auth = checkAuthStatus()
    if (auth.isAuthenticated) {
      setView('main')
    } else {
      setView('login')
    }
  }, [setShowPrivacyModal, setView])

  const handlePrivacyReject = useCallback(() => {
    setShowPrivacyModal(false)
    setView('login')
  }, [setShowPrivacyModal, setView])

  const handleSelectSession = useCallback(async (session: Session) => {
    setCurrentSession(session)
    setCurrentSessionId(session.id)
    setShowSessionList(false)

    const messages = await dbManager.getMessages(session.id)
    initSessionMessages(session.id, messages)
    // 标记该会话所有 AI 消息为已读，清除未读计数
    markSessionAsRead(session.id)

    await dbManager.saveSession({ ...session, unreadCount: 0 })
    showToast(`已切换到会话：${session.name}`)
  }, [setCurrentSession, setCurrentSessionId, setShowSessionList, initSessionMessages, markSessionAsRead, showToast])

  // 【#12 修复】会话删除兜底：删掉当前会话（或清空全部）时自动切到剩余的第一个
  // 会话；一个不剩则清空状态显示空态，避免停在已删除会话的空白页、消息无处发送
  const handleSessionDeleted = useCallback((deletedId: string, remaining: Session[]) => {
    // 删除的是其他会话：无需动作（deletedId 为空表示全部清空，必须处理）
    if (deletedId && currentSession?.id && deletedId !== currentSession.id) return
    if (remaining.length > 0) {
      handleSelectSession(remaining[0])
    } else {
      setCurrentSession(null)
      setCurrentSessionId('')
    }
  }, [currentSession?.id, handleSelectSession, setCurrentSession, setCurrentSessionId])

  const handleToggleTheme = useCallback(() => {
    // 【方案一】品牌视觉固定为梦幻浅色主题，不再循环切换深色
    applyTheme('light')
    localStorage.setItem('miaoyi_theme', 'light')
    showToast('喵艺始终使用梦幻浅色主题～')
  }, [showToast])

  const handleCheckin = useCallback(async () => {
    if (dailyCheckin) return
    // 乐观更新 UI（即时反馈）
    setDailyCheckin(true)
    const currentFood = parseInt(localStorage.getItem('catFood') || '0')

    try {
      // 【后端同步】调用后端签到API，数据存储到DB（跨设备一致）
      const result = await checkin()
      if (result.code === 200 && result.data) {
        const streak = result.data.streak || 1
        localStorage.setItem('checkinStreak', streak.toString())
        localStorage.setItem('lastCheckinDate', new Date().toDateString())

        // 成就检查
        const achievements = JSON.parse(localStorage.getItem('achievements') || '[]')
        if (!achievements.includes('daily_checkin')) {
          achievements.push('daily_checkin')
        }
        if (streak >= 7 && !achievements.includes('streak_7')) {
          achievements.push('streak_7')
          showToast('🎉 解锁成就：连续签到7天！', 'success')
        }
        localStorage.setItem('achievements', JSON.stringify(achievements))
        localStorage.setItem('catFood', (currentFood + 10).toString())

        showToast(`签到成功！连续${streak}天，获得10猫粮 🐟`, 'success')
      } else {
        // 后端返回错误（如已签到）→ 回滚
        setDailyCheckin(false)
        showToast(result.message || '今天已经签到过了哦～', 'warning')
      }
    } catch (e) {
      // 网络错误：保留本地签到（降级），下次上线同步
      localStorage.setItem('lastCheckinDate', new Date().toDateString())
      localStorage.setItem('catFood', (currentFood + 10).toString())
      showToast('签到成功（本地），网络恢复后同步', 'success')
    }
  }, [dailyCheckin, showToast])

  // 【合规】连续使用时长计时：每 30 秒 tick 一次，仅在前台可见时累计；
  // 当天累计达 120 分钟 → 弹出强制休息提醒（确认后重置当天计时）
  const screenTimeShownRef = useRef(false)
  useEffect(() => {
    const ACTIVE_KEY = 'miaoyi_active_ms'
    const DAY_KEY = 'miaoyi_active_day'
    const LIMIT_MINUTES = 120

    const todayStr = new Date().toDateString()
    if (localStorage.getItem(DAY_KEY) !== todayStr) {
      localStorage.setItem(DAY_KEY, todayStr)
      localStorage.setItem(ACTIVE_KEY, '0')
    }

    const timer = setInterval(() => {
      if (document.visibilityState !== 'visible') return
      const prev = parseInt(localStorage.getItem(ACTIVE_KEY) || '0', 10)
      const next = prev + 30_000
      localStorage.setItem(ACTIVE_KEY, String(next))
      if (!screenTimeShownRef.current && next >= LIMIT_MINUTES * 60_000) {
        screenTimeShownRef.current = true
        setScreenTimeMinutes(Math.round(next / 60_000))
        setShowScreenTimeReminder(true)
      }
    }, 30_000)
    return () => clearInterval(timer)
  }, [])

  const handleScreenTimeConfirm = useCallback(() => {
    setShowScreenTimeReminder(false)
    screenTimeShownRef.current = false
    localStorage.setItem('miaoyi_active_ms', '0')
    localStorage.setItem('miaoyi_active_day', new Date().toDateString())
  }, [])

  useEffect(() => {
    const lastCheckin = localStorage.getItem('lastCheckinDate')
    if (lastCheckin === new Date().toDateString()) {
      setDailyCheckin(true)
    }
    
    const today = new Date()
    const yesterday = new Date(today)
    yesterday.setDate(yesterday.getDate() - 1)
    
    if (lastCheckin && lastCheckin !== yesterday.toDateString() && lastCheckin !== today.toDateString()) {
      localStorage.setItem('checkinStreak', '0')
    }
  }, [])

  return (
    <div className={`app-container ${isSidebarOpen ? 'sidebar-open' : ''} ${view === 'main' && currentTab === 'chat' ? 'chat-tab' : ''}`}>
      <div className={`network-alert ${!isOnline ? 'show' : ''}`}>
        ⚠️ 当前网络不可用
      </div>

      {/* 【合规】未成年人模式常驻提示横幅 */}
      {view === 'main' && isLoggedIn && minorMode && (
        <div className="minor-mode-banner">🧸 未成年人模式已开启 · 已启用适龄内容保护</div>
      )}

      <PerformanceMonitor isVisible={false} />

      <CallScreen />  {/* 通话浮层，idle 时不渲染（enhance 模块）*/}

      {view === 'splash' && <SplashScreen onComplete={handleSplashComplete} />}

      {view === 'login' && <LoginForm onLogin={handleLogin} onShowToast={showToast} />}

      {view === 'main' && isLoggedIn && (
        <>
          {currentTab === 'home' && (
            <Suspense fallback={<Skeleton />}>
            <HomePage
              emotionalState={emotionalState}
              growthData={growthData}
              dailyFragment={dailyFragment}
              onNavigate={setCurrentTab}
              onOpenSettings={() => setShowSettings(true)}
            />
            </Suspense>
          )}

          {currentTab === 'chat' && (
            <>
              {onboardingData && (
                <Onboarding
                  greeting={onboardingData.greeting}
                  options={onboardingData.options}
                  onSelectOption={(triggerText) => {
                    sendMessage(triggerText)
                    setOnboardingData(null)
                  }}
                  onSkip={() => {
                    setOnboardingData(null)
                  }}
                />
              )}
              
              <ChatView
                messages={getMessages(currentSessionId)}
                connectionStatus={connectionStatus}
                emotionalState={emotionalState}
                onSendMessage={sendMessage}
                onToggleSidebar={toggleSidebar}
                onToggleTheme={handleToggleTheme}
                onShowToast={showToast}
                onReconnect={reconnect}
                onToggleSessionList={toggleSessionList}
                onBackToHome={() => setCurrentTab('home')}
                currentSessionName={currentSession?.name || '喵艺'}
                onUpdateMessages={(msgs) => setMessages(currentSessionId, msgs)}
                isAdmin={isAdmin}
                onOpenAdminPanel={() => setShowAdminPanel(true)}
                isTyping={isTyping}
                thinkingStatus={thinkingStatus}
                thinkingStep={thinkingStep}
                thinkingProgress={thinkingProgress}
                thinkingStepIndex={thinkingStepIndex}
                thinkingTotalSteps={thinkingTotalSteps}
                failedCount={failedMessageIds.length}
                onRetryAll={() => retryAllFailed(sendMessage)}
                onRetryMessage={retryMessage}
                onMarkSessionRead={() => markSessionAsRead(currentSessionId)}
                onOpenServerSettings={() => {
                  // 断连引导：打开设置并直接展开服务器地址弹窗
                  setOpenServerModal(true)
                  setShowSettings(true)
                }}
                dailyFragment={dailyFragment}
              />

              <Sidebar
                isOpen={isSidebarOpen}
                onClose={() => setIsSidebarOpen(false)}
                growthData={growthData}
                emotionalState={emotionalState}
                onShowToast={showToast}
                onLogout={handleLogout}
              />

              <div className={`sidebar-mask ${showSessionList ? 'show' : ''}`} onClick={() => setShowSessionList(false)} />
              <div className={`session-list-overlay ${showSessionList ? 'show' : ''}`}>
                <SessionList
                  isOpen={showSessionList}
                  currentSessionId={currentSession?.id}
                  onSelectSession={handleSelectSession}
                  onShowToast={showToast}
                  messagesBySession={messagesBySession}
                  onClose={() => setShowSessionList(false)}
                  onSessionDeleted={handleSessionDeleted}
                />
              </div>
            </>
          )}

          {currentTab === 'memory' && (
            <Suspense fallback={<Skeleton />}><MemoryPage onShowToast={showToast} /></Suspense>
          )}

          {currentTab === 'miaoyi' && (
            <Suspense fallback={<Skeleton />}>
            <MiaoyiPage
              emotionalState={emotionalState}
              growthData={growthData}
            />
            </Suspense>
          )}

          {currentTab === 'profile' && (
            <Suspense fallback={<Skeleton />}>
            <ProfilePage
              onShowToast={showToast}
              onOpenSettings={() => setShowSettings(true)}
              onCheckin={handleCheckin}
              dailyCheckin={dailyCheckin}
            />
            </Suspense>
          )}

          {/* 聊天页进入沉浸式：隐藏底部导航（返回入口在聊天页左上角） */}
          {currentTab !== 'chat' && (
            <BottomNav currentTab={currentTab} onTabChange={setCurrentTab} />
          )}
        </>
      )}

      <PrivacyModal isOpen={showPrivacyModal} onClose={handlePrivacyReject} onAgree={handlePrivacyAgree} />

      {/* 【合规】连续使用 2 小时强制休息提醒 */}
      {showScreenTimeReminder && (
        <ScreenTimeReminder minutes={screenTimeMinutes} onConfirm={handleScreenTimeConfirm} />
      )}

      {showAdminPanel && <Suspense fallback={null}><AdminPanel onClose={() => setShowAdminPanel(false)} /></Suspense>}

      {toast && <Toast message={toast.message} type={toast.type} onComplete={hideToast} />}
      
      {/* 新手引导蒙层 */}
      <OnboardingGuide
        isOpen={showGuide}
        onComplete={() => setShowGuide(false)}
      />

      {/* 设置弹窗（懒加载分包） */}
      <Suspense fallback={null}>
      <SettingsModal
        isOpen={showSettings}
        onClose={() => {
          setShowSettings(false)
          setOpenServerModal(false)
        }}
        onShowToast={showToast}
        onThemeChange={() => {}}
        openServerModal={openServerModal}
      />
      </Suspense>

      {/* App 更新弹窗（可选更新可跳过；强制更新不可关闭） */}
      {updateInfo && (
        <UpdateDialog
          info={updateInfo}
          onLater={() => {
            localStorage.setItem('miaoyi_skipped_update', String(updateInfo.latest_version_code))
            setUpdateInfo(null)
          }}
        />
      )}

      {/* 登录过渡动画 - 猫爪印扩散 */}
      {showLoginTransition && (
        <div className="login-transition-overlay">
          <div className="transition-paws-container">
            <span className="transition-paw paw-1">🐾</span>
            <span className="transition-paw paw-2">🐾</span>
            <span className="transition-paw paw-3">🐾</span>
            <span className="transition-paw paw-4">🐾</span>
            <span className="transition-paw paw-5">🐾</span>
            <span className="transition-paw paw-6">🐾</span>
            <div className="transition-center-paw">🐱</div>
          </div>
          {showWelcomeText && (
            <div className="transition-welcome-text">
              <span className="welcome-cat">🐱</span>
              <span className="welcome-msg">欢迎回来，铲屎官～</span>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default App