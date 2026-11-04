import { getBaseUrl } from './api'

export const requestNotificationPermission = async (): Promise<NotificationPermission> => {
  if (!('Notification' in window)) {
    console.warn('浏览器不支持通知功能')
    return 'denied'
  }

  if (Notification.permission === 'granted') {
    return 'granted'
  }

  if (Notification.permission === 'denied') {
    return 'denied'
  }

  return await Notification.requestPermission()
}

export const showNotification = (title: string, options: NotificationOptions = {}): void => {
  if (!('Notification' in window) || Notification.permission !== 'granted') {
    return
  }

  new Notification(title, {
    icon: '/icon-192.png',
    badge: '/icon-192.png',
    ...options,
  })
}

export const sendPushNotification = async (
  title: string,
  body: string,
  data?: Record<string, string>
): Promise<void> => {
  if ('serviceWorker' in navigator) {
    const registration = await navigator.serviceWorker.ready
    if (registration.pushManager) {
      const subscription = await registration.pushManager.getSubscription()
      if (subscription) {
        try {
          await fetch(`${getBaseUrl()}/push/notify`, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
            },
            body: JSON.stringify({
              subscription: subscription.toJSON(),
              title,
              body,
              data,
            }),
          })
        } catch (error) {
          console.error('推送通知失败:', error)
        }
      }
    }
  }
}

export const subscribeToPush = async (): Promise<void> => {
  if (!('serviceWorker' in navigator)) {
    return
  }

  try {
    const registration = await navigator.serviceWorker.ready
    const existingSubscription = await registration.pushManager.getSubscription()
    
    if (existingSubscription) {
      return
    }

    const response = await fetch(`${getBaseUrl()}/push/vapid-key`)
    const json = await response.json()
    // 【串联修复】后端 create_response 包装为 {code,data:{publicKey}}，兼容裸 {publicKey}
    const publicKey = json?.data?.publicKey ?? json?.publicKey
    if (!publicKey) {
      console.warn('未配置 VAPID 公钥，跳过推送订阅')
      return
    }

    const subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(publicKey) as BufferSource,
    })

    await fetch(`${getBaseUrl()}/push/subscribe`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(subscription.toJSON()),
    })
  } catch (error) {
    console.error('订阅推送失败:', error)
  }
}

export const unsubscribeFromPush = async (): Promise<void> => {
  if (!('serviceWorker' in navigator)) {
    return
  }

  try {
    const registration = await navigator.serviceWorker.ready
    const subscription = await registration.pushManager.getSubscription()
    
    if (subscription) {
      await subscription.unsubscribe()
      
      await fetch(`${getBaseUrl()}/push/unsubscribe`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(subscription.toJSON()),
      })
    }
  } catch (error) {
    console.error('取消订阅推送失败:', error)
  }
}

function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4)
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/')
  const rawData = window.atob(base64)
  const buffer = new ArrayBuffer(rawData.length)
  const outputArray = new Uint8Array(buffer)
  
  for (let i = 0; i < rawData.length; ++i) {
    outputArray[i] = rawData.charCodeAt(i)
  }
  
  return outputArray
}