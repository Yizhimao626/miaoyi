import { useEffect, useRef } from 'react'
import { App as CapacitorApp } from '@capacitor/app'
import { useUIStore } from '../store/uiStore'
import { useCallStore } from '../store/callStore'

export type MainTab = 'home' | 'chat' | 'memory' | 'miaoyi' | 'profile'

interface UseBackButtonOptions {
  /** 是否处于已登录主界面（登录/启动页不拦截，交给系统默认退出行为） */
  active: boolean
  currentTab: MainTab
  setCurrentTab: (tab: MainTab) => void
  /** 全局设置弹窗 */
  showSettings: boolean
  setShowSettings: (show: boolean) => void
}

/**
 * 【移动适配】Android 物理返回键拦截。
 *
 * 旧版零处理：任何浮层/子页面按返回键都会直接退出 App。
 * 按"最上层优先"逐级消费返回事件：
 *   通话中 → 挂断；设置/管理面板、会话抽屉、个人侧栏 → 关闭；
 *   其他 Tab → 回到聊天 Tab；已在聊天 Tab → App 退到后台（保活，不销毁）。
 *
 * 仅 Capacitor 原生环境生效；浏览器中该事件不存在，注册调用会被插件忽略。
 * 状态统一在回调内通过 getState() / ref 读取，监听器只注册一次。
 */
export function useBackButton({
  active,
  currentTab,
  setCurrentTab,
  showSettings,
  setShowSettings,
}: UseBackButtonOptions) {
  // App 本地 state 的 ref 镜像（currentTab / showSettings 不在全局 store 中）
  const currentTabRef = useRef(currentTab)
  const showSettingsRef = useRef(showSettings)
  const setCurrentTabRef = useRef(setCurrentTab)
  const setShowSettingsRef = useRef(setShowSettings)
  currentTabRef.current = currentTab
  showSettingsRef.current = showSettings
  setCurrentTabRef.current = setCurrentTab
  setShowSettingsRef.current = setShowSettings

  useEffect(() => {
    if (!active) return

    const handle = async () => {
      const ui = useUIStore.getState()
      const call = useCallStore.getState()

      // 1. 通话进行中（含来电响铃）：挂断/拒接，不退出
      if (call.status !== 'idle' && call.status !== 'ended') {
        await call.endCall('用户按返回键挂断')
        return
      }
      // 2. 设置弹窗
      if (showSettingsRef.current) {
        setShowSettingsRef.current(false)
        return
      }
      // 3. 管理员面板
      if (ui.showAdminPanel) {
        ui.setShowAdminPanel(false)
        return
      }
      // 4. 会话列表抽屉
      if (ui.showSessionList) {
        ui.setShowSessionList(false)
        return
      }
      // 5. 个人侧栏
      if (ui.isSidebarOpen) {
        ui.setIsSidebarOpen(false)
        return
      }
      // 6. 非聊天 Tab：回到聊天主 Tab（返回键回主界面，而非退出）
      if (currentTabRef.current !== 'chat') {
        setCurrentTabRef.current('chat')
        return
      }
      // 7. 已在聊天主界面且无浮层：退到后台保活（下次打开秒回），不直接退出
      CapacitorApp.minimizeApp()
    }

    const unbindPromise = CapacitorApp.addListener('backButton', handle)
    return () => {
      unbindPromise.then((listener) => listener.remove()).catch(() => {})
    }
  }, [active])
}
