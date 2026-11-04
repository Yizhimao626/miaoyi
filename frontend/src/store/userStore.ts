import { create } from 'zustand'
import type { Session, UserProfile } from '../types'

interface UserState {
  isLoggedIn: boolean
  isAdmin: boolean
  currentSession: Session | null
  userProfile: UserProfile | null
  setIsLoggedIn: (loggedIn: boolean) => void
  setIsAdmin: (admin: boolean) => void
  setCurrentSession: (session: Session | null) => void
  setUserProfile: (profile: UserProfile | null) => void
}

export const useUserStore = create<UserState>((set) => ({
  isLoggedIn: false,
  isAdmin: false,
  currentSession: null,
  userProfile: null,

  setIsLoggedIn: (loggedIn) => set({ isLoggedIn: loggedIn }),
  setIsAdmin: (admin) => set({ isAdmin: admin }),
  setCurrentSession: (session) => set({ currentSession: session }),
  setUserProfile: (profile) => set({ userProfile: profile }),
}))