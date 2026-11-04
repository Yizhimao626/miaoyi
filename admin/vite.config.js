import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发时代理 /api 到本地后端（生产构建后由 nginx 反代）
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5174,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 1500,
  },
})
