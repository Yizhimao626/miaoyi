import { useState, useEffect, useCallback } from 'react'

export function useNetwork(): {
  isOnline: boolean
  offlineSince: number | null
} {
  const [isOnline, setIsOnline] = useState(navigator.onLine)
  const [offlineSince, setOfflineSince] = useState<number | null>(null)

  const handleOnline = useCallback(() => {
    setIsOnline(true)
    setOfflineSince(null)
  }, [])

  const handleOffline = useCallback(() => {
    setIsOnline(false)
    setOfflineSince(Date.now())
  }, [])

  useEffect(() => {
    window.addEventListener('online', handleOnline)
    window.addEventListener('offline', handleOffline)

    return () => {
      window.removeEventListener('online', handleOnline)
      window.removeEventListener('offline', handleOffline)
    }
  }, [handleOnline, handleOffline])

  return { isOnline, offlineSince }
}