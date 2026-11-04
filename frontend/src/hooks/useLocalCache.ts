import { useState, useEffect, useCallback } from 'react'
import type { Message } from '../types'

const STORAGE_KEY = 'miaoyi_messages_cache'
const MAX_CACHE_SIZE = 100
const CACHE_VERSION = '1.0.0'
const MAX_CACHE_AGE = 24 * 60 * 60 * 1000

interface CacheEntry {
  messages: Message[]
  timestamp: number
  sessionId: string
  version: string
}

export function useLocalCache(sessionId: string) {
  const [cachedMessages, setCachedMessages] = useState<Message[]>([])
  const [isCacheReady, setIsCacheReady] = useState(false)

  const loadCache = useCallback(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY)
      if (!stored) {
        setIsCacheReady(true)
        return
      }

      const cache: CacheEntry = JSON.parse(stored)
      
      if (cache.version !== CACHE_VERSION) {
        console.warn('[Cache] Cache version mismatch, clearing old cache')
        localStorage.removeItem(STORAGE_KEY)
        setIsCacheReady(true)
        return
      }

      if (cache.sessionId !== sessionId) {
        setIsCacheReady(true)
        return
      }

      if (!cache.messages || !Array.isArray(cache.messages)) {
        console.warn('[Cache] Invalid cache structure')
        setIsCacheReady(true)
        return
      }

      const age = Date.now() - cache.timestamp
      if (age > MAX_CACHE_AGE) {
        console.warn('[Cache] Cache expired')
        localStorage.removeItem(STORAGE_KEY)
        setIsCacheReady(true)
        return
      }

      setCachedMessages(cache.messages.slice(-MAX_CACHE_SIZE))
    } catch (error) {
      console.warn('[Cache] Failed to load cache:', error)
      try {
        localStorage.removeItem(STORAGE_KEY)
      } catch {
      }
    } finally {
      setIsCacheReady(true)
    }
  }, [sessionId])

  const saveCache = useCallback((messages: Message[]) => {
    try {
      const cache: CacheEntry = {
        messages: messages.slice(-MAX_CACHE_SIZE),
        timestamp: Date.now(),
        sessionId,
        version: CACHE_VERSION,
      }
      localStorage.setItem(STORAGE_KEY, JSON.stringify(cache))
    } catch (error) {
      console.warn('[Cache] Failed to save cache:', error)
    }
  }, [sessionId])

  const clearCache = useCallback(() => {
    try {
      localStorage.removeItem(STORAGE_KEY)
      setCachedMessages([])
    } catch (error) {
      console.warn('[Cache] Failed to clear cache:', error)
    }
  }, [])

  useEffect(() => {
    loadCache()
  }, [loadCache])

  return {
    cachedMessages,
    isCacheReady,
    saveCache,
    clearCache,
  }
}
