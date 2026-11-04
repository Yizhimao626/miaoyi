const STORAGE_KEYS = {
  USERNAME: 'miaoyi_username',
  THEME: 'miaoyi_theme',
  MUTE: 'miaoyi_mute',
  PRIVACY_AGREED: 'miaoyi_privacy_agreed',
  MINOR_MODE: 'miaoyi_minor_mode',
}

// 【P2清理】旧版 getToken/setToken/removeToken/clearAuth 与 cookie 逻辑已删除：
// token 统一由 utils/auth.ts 管理（miaoyi_access_token/refresh_token 三件套），
// 这套残留的是早期方案（miaoyi_token + cookie），对现行 token 是 no-op。

export function setUsername(username: string): void {
  localStorage.setItem(STORAGE_KEYS.USERNAME, username)
}

export type ThemeMode = 'light' | 'dark' | 'system'

export function getTheme(): ThemeMode {
  const saved = localStorage.getItem(STORAGE_KEYS.THEME)
  return (saved as ThemeMode) || 'system'
}

export function setTheme(mode: ThemeMode): void {
  localStorage.setItem(STORAGE_KEYS.THEME, mode)
}

export function getMute(): boolean {
  const saved = localStorage.getItem(STORAGE_KEYS.MUTE)
  return saved === 'true'
}

export function setMute(muted: boolean): void {
  localStorage.setItem(STORAGE_KEYS.MUTE, String(muted))
}

export function getPrivacyAgreed(): boolean {
  const saved = localStorage.getItem(STORAGE_KEYS.PRIVACY_AGREED)
  return saved === 'true'
}

export function setPrivacyAgreed(agreed: boolean): void {
  localStorage.setItem(STORAGE_KEYS.PRIVACY_AGREED, String(agreed))
}

/** 【合规】未成年人模式开关（持久化） */
export function getMinorMode(): boolean {
  return localStorage.getItem(STORAGE_KEYS.MINOR_MODE) === 'true'
}

export function setMinorMode(on: boolean): void {
  localStorage.setItem(STORAGE_KEYS.MINOR_MODE, String(on))
}

export function applyTheme(mode: ThemeMode): void {
  // 【方案一·品牌决策】喵艺坚持『治愈系浅色梦幻』视觉，全局强制浅色主题，
  // 不受手机系统深色模式影响。参数保留以兼容旧调用方，但一律落地为 light。
  void mode
  const html = document.documentElement
  html.setAttribute('data-theme', 'light')
  html.style.colorScheme = 'light'
}

let themeListener: (() => void) | null = null

export function subscribeToSystemTheme(): void {
  if (themeListener) return
  
  const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
  
  const handleChange = () => {
    const currentTheme = getTheme()
    if (currentTheme === 'system') {
      applyTheme('system')
    }
  }
  
  mediaQuery.addEventListener('change', handleChange)
  themeListener = handleChange
}

export function unsubscribeFromSystemTheme(): void {
  if (!themeListener) return
  
  const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
  mediaQuery.removeEventListener('change', themeListener)
  themeListener = null
}

export function getNickname(): string | null {
  return localStorage.getItem('miaoyi_nickname')
}

export function setNickname(nickname: string): void {
  localStorage.setItem('miaoyi_nickname', nickname)
}

export function escapeHtml(text: string): string {
  if (!text) return ''
  const div = document.createElement('div')
  div.textContent = text
  return div.innerHTML
}