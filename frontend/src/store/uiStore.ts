import { create } from 'zustand'

interface ToastState {
  message: string
  type: 'success' | 'error' | 'warning' | 'info'
}

interface UIState {
  view: 'splash' | 'login' | 'main'
  isSidebarOpen: boolean
  toast: ToastState | null
  showPrivacyModal: boolean
  showSessionList: boolean
  showAdminPanel: boolean
  setView: (view: UIState['view']) => void
  setIsSidebarOpen: (open: boolean) => void
  toggleSidebar: () => void
  showToast: (message: string, type?: ToastState['type']) => void
  hideToast: () => void
  setShowPrivacyModal: (show: boolean) => void
  setShowSessionList: (show: boolean) => void
  toggleSessionList: () => void
  setShowAdminPanel: (show: boolean) => void
}

export const useUIStore = create<UIState>((set) => ({
  view: 'splash',
  isSidebarOpen: false,
  toast: null,
  showPrivacyModal: false,
  showSessionList: false,
  showAdminPanel: false,

  setView: (view) => set({ view }),

  setIsSidebarOpen: (open) => set({ isSidebarOpen: open }),

  toggleSidebar: () => set((state) => ({ isSidebarOpen: !state.isSidebarOpen })),

  showToast: (message, type = 'info') => set({ toast: { message, type } }),

  hideToast: () => set({ toast: null }),

  setShowPrivacyModal: (show) => set({ showPrivacyModal: show }),

  setShowSessionList: (show) => set({ showSessionList: show }),

  toggleSessionList: () => set((state) => ({ showSessionList: !state.showSessionList })),

  setShowAdminPanel: (show) => set({ showAdminPanel: show }),
}))