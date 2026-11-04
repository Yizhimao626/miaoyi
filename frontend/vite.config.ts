import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 后端端口 - 优先从环境变量或端口文件读取
import fs from 'fs'
import path from 'path'

function getBackendPort(): string {
  if (process.env.BACKEND_PORT) {
    return process.env.BACKEND_PORT
  }
  // 尝试读取服务器端口文件（服务器启动时会写入到 mobile-chat/.server-port）
  const portFile = path.resolve(__dirname, '.server-port')
  try {
    if (fs.existsSync(portFile)) {
      const port = fs.readFileSync(portFile, 'utf-8').trim()
      if (port) return port
    }
  } catch {}
  return '8001' // 默认端口
}

const BACKEND_PORT = getBackendPort()
// 默认 127.0.0.1 而非 localhost：Node 17+/22+ 对 localhost 可能优先解析为 IPv6 ::1，
// 而 uvicorn 仅监听 IPv4（0.0.0.0），Windows 下 IPv6 回环可能被静默丢弃形成黑洞连接，
// 导致 Vite 代理转发超时。127.0.0.1 直连 IPv4 稳定可靠。
const BACKEND_HOST = process.env.BACKEND_HOST || '127.0.0.1'

console.log(`[vite.config] 后端地址: http://${BACKEND_HOST}:${BACKEND_PORT}`)

export default defineConfig({
  plugins: [react()],
  // esbuild 压缩（Vite 内置，无需额外安装 terser 依赖）
  esbuild: {
    pure: ['console.log', 'console.debug', 'console.info'],
  },
  // 生产构建优化
  build: {
    minify: 'esbuild',
    // 【兼容性修复】Vite 6 默认构建目标为 Chrome 107+，旧款手机的 Android
    // System WebView 无法解析新语法 → bundle 直接失败，表现为永远卡在启动
    // 加载页（预加载器不消失、登录页出不来）。降级到 ES2018（约 Chrome 64 /
    // Android WebView 66+），esbuild 会把可选链、空值合并等语法转译掉。
    target: 'es2018',
    // 【兼容性修复 2026-10-02】CSS 压缩目标默认不跟随 target（仍按新语法输出），
    // 产物出现 inset:0、#0006（8位hex）等简写——旧 WebView 不识别 inset 时
    // .sidebar-mask 等 fixed 全屏遮罩会塌缩成 0×0：遮罩不显示、点击穿透，
    // 表现为"会话抽屉收不回去/点遮罩无反应"。显式降级让 esbuild 展开 inset、
    // 转换 8 位 hex 为 rgba()。
    cssTarget: 'chrome61',
    rollupOptions: {
      output: {
        // 代码分包：第三方库独立 chunk，利用浏览器长效缓存
        manualChunks: {
          'vendor-react': ['react', 'react-dom'],
          'vendor-state': ['zustand'],
        },
      },
    },
  },
  server: {
    host: '::',                 // 双栈监听(IPv6+IPv4)：Windows 下 IPv6 回环可能被丢包形成黑洞，
                                // host 用 '::' 让 localhost(→::1) 也能连通；Node 默认双栈，IPv4 同样接受
    port: 5173,
    strictPort: false,          // 5173 占用时自动换端口
    // 【历史教训】不要强制设置 hmr.host：
    // - PC 浏览器访问 localhost 时 HMR 需连 localhost
    // - 手机访问 IP 时 HMR 需连 IP
    // Vite 默认会用 window.location.hostname 自动决定 HMR 连接地址，
    // 强制设成 LAN_IP 会导致 PC 端 HMR 报 400 错误，热更新失效。
    // 只保留 overlay（错误遮罩），其余交给 Vite 自动处理。
    hmr: {
      overlay: true,
    },
    headers: {
      'Cache-Control': 'no-store, no-cache, must-revalidate, proxy-revalidate',
      'Pragma': 'no-cache',
      'Expires': '0',
    },
    proxy: {
      '/api': {
        target: `http://${BACKEND_HOST}:${BACKEND_PORT}`,
        changeOrigin: true,
        secure: false,
        // 防止代理超时（长思考、生成语音慢的接口）
        timeout: 300000,
        proxyTimeout: 300000,
        // 【修复】静默处理代理层良性错误，避免刷屏
        configure: (proxy) => {
          proxy.on('error', (err: any) => {
            const code = err?.code || ''
            if (['ECONNABORTED', 'ECONNRESET', 'EPIPE', 'ECONNREFUSED'].includes(code)) {
              // 良性错误：客户端断开/后端重启/页面刷新，不需要 stack trace
              return
            }
            console.warn(`[vite] /api proxy error (${code}):`, err.message)
          })
        },
      },
      '/ws': {
        target: `ws://${BACKEND_HOST}:${BACKEND_PORT}`,
        ws: true,
        changeOrigin: true,
        // WebSocket 长连接：禁用超时
        timeout: 0,
        proxyTimeout: 0,
        // 【修复】静默处理 WS 代理层良性错误
        // ECONNABORTED/ECONNRESET 发生在 WebSocket 断开瞬间（页面刷新/HMR 重载/后端重启），
        // Vite 代理向已关闭的 socket 写入数据时触发，属于正常现象，不影响功能。
        // 旧版没有 error 处理，导致大量 stack trace 刷屏。
        configure: (proxy) => {
          proxy.on('error', (err: any) => {
            const code = err?.code || ''
            if (['ECONNABORTED', 'ECONNRESET', 'EPIPE', 'ECONNREFUSED'].includes(code)) {
              // 良性错误：WS 断开时的写入失败，静默处理
              return
            }
            console.warn(`[vite] /ws proxy error (${code}):`, err.message)
          })
        },
      },
      '/voice_cache': {
        target: `http://${BACKEND_HOST}:${BACKEND_PORT}`,
        changeOrigin: true,
        secure: false,
      },
    },
  },
})
