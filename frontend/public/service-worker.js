/**
 * 喵艺 Service Worker
 *
 * 【历史教训】旧版 SW 缓存了 Vite 开发模式的 JS 模块，
 * 导致源代码修改后浏览器仍然加载旧 bundle（如旧的 VITE_WS_URL=ws://10.48.236.124:8002/ws），
 * 所有 WebSocket 修复都不生效。
 *
 * 修复策略：
 * 1. 升级缓存版本号 v1 → v2，强制清理旧缓存
 * 2. 只缓存生产环境的静态资源（HTML/图标/manifest），不缓存 JS/CSS（开发模式由 Vite 实时编译）
 * 3. 开发环境（localhost / 127.0.0.1 / IP:5173）完全不拦截 fetch，让请求直达 Vite
 */

const CACHE_VERSION = 'miaoyi-v3'
const STATIC_ASSETS = [
  '/',
  '/index.html',
  '/manifest.json',
  '/icon-192.png',
  '/icon-512.png',
]

// 判断是否为开发环境（Vite dev server 端口 5173）
function isDevEnvironment(url) {
  try {
    const u = new URL(url)
    // Vite dev server 默认 5173，或常见开发端口
    return u.port === '5173' || u.port === '5174' || u.port === '5175' ||
           u.hostname === 'localhost' || u.hostname === '127.0.0.1'
  } catch {
    return false
  }
}

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION).then((cache) => {
      return cache.addAll(STATIC_ASSETS).catch((err) => {
        // 单个资源加载失败不阻塞 SW 安装
        console.warn('[SW] 部分静态资源预缓存失败:', err)
      })
    }).then(() => {
      // 强制立即激活新版本，不等旧 SW 退出
      return self.skipWaiting()
    })
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      // 删除所有旧版本缓存（包括旧的 miaoyi-v1）
      return Promise.all(
        cacheNames.filter((name) => name !== CACHE_VERSION).map((name) => {
          console.log('[SW] 清理旧缓存:', name)
          return caches.delete(name)
        })
      )
    }).then(() => {
      // 立即接管所有客户端
      return self.clients.claim()
    }).then(() => {
      // 通知所有客户端有新 SW 激活，建议刷新页面
      return self.clients.matchAll().then((clients) => {
        clients.forEach((client) => {
          client.postMessage({ type: 'SW_UPDATED' })
        })
      })
    })
  )
})

self.addEventListener('fetch', (event) => {
  const request = event.request

  // 开发环境：完全不拦截，让 Vite 处理所有请求
  if (isDevEnvironment(request.url)) {
    return
  }

  // 非 GET 请求不拦截
  if (request.method !== 'GET') {
    return
  }

  // API 和 WebSocket 请求不拦截
  if (request.url.includes('/api/') || request.url.includes('/ws') || request.url.includes('/wss')) {
    return
  }

  // 【2026-10 修复】HTML/导航请求必须 network-first：
  // 旧版对 index.html 缓存优先且不回源，每次部署后旧 HTML 引用已删除的
  // hash chunk，导致"Failed to fetch dynamically imported module"整页白屏。
  const urlPath = new URL(request.url).pathname
  const isHtml = request.mode === 'navigate' || urlPath === '/' || urlPath === '/index.html'
  if (isHtml) {
    event.respondWith(
      fetch(request)
        .then((networkResponse) => {
          if (networkResponse && networkResponse.status === 200) {
            const clone = networkResponse.clone()
            caches.open(CACHE_VERSION).then((cache) => cache.put('/index.html', clone)).catch(() => {})
          }
          return networkResponse
        })
        .catch(() => caches.match(request).then((r) => r || caches.match('/index.html')))
    )
    return
  }

  // 生产环境：仅缓存静态资源（HTML/图标/manifest），不缓存 JS/CSS
  // JS/CSS 由 Vite 构建时生成带 hash 的文件名，浏览器自带缓存策略
  const url = new URL(request.url)
  const isStaticAsset = STATIC_ASSETS.includes(url.pathname) ||
                        url.pathname.endsWith('.png') ||
                        url.pathname.endsWith('.ico') ||
                        url.pathname.endsWith('.json')

  if (!isStaticAsset) {
    return
  }

  event.respondWith(
    caches.match(request).then((cachedResponse) => {
      if (cachedResponse) {
        return cachedResponse
      }

      return fetch(request).then((networkResponse) => {
        if (!networkResponse || networkResponse.status !== 200) {
          return networkResponse
        }

        const responseToCache = networkResponse.clone()
        caches.open(CACHE_VERSION).then((cache) => {
          cache.put(request, responseToCache).catch(() => {})
        })

        return networkResponse
      }).catch(() => {
        return caches.match('/')
      })
    })
  )
})

self.addEventListener('push', (event) => {
  const data = event.data?.json() || {}
  const title = data.title || '喵艺有新消息'
  const options = {
    body: data.body || '',
    icon: '/icon-192.png',
    badge: '/icon-192.png',
    data: data.data || {},
  }

  event.waitUntil(
    self.registration.showNotification(title, options)
  )
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()

  event.waitUntil(
    clients.openWindow('/')
  )
})
