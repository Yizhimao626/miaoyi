import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.miaoyi.app',
  appName: '喵艺',
  webDir: 'dist',
  server: {
    // 【云服务器登录修复 2026-10-01】androidScheme 改回 http：
    // 真机实测 https://localhost 页面 fetch http://<backend> 被混合内容拦截
    // （内核探针证实 App 零发包，MainActivity 的 ALWAYS_ALLOW 兜底未生效）。
    // http://localhost 页面请求 http 后端不存在混合内容问题，登录可用。
    // 注意：navigator.mediaDevices 在 http://localhost 下仍是安全上下文（规范定义
    // loopback 可信），录音理论上不受影响；若录音回归再评估 HTTPS 反代方案。
    androidScheme: 'http',
    // 【App 打包修复】移除硬编码内网 IP：后端地址由用户在「设置 → 服务器地址」中配置，
    // 前端 api.ts/useWebSocket 会读取该配置并拼接到所有请求。
    // cleartext: true 允许 App 通过明文 HTTP 访问任意配置的后端地址。
    allowNavigation: ['localhost', '127.0.0.1'],
    cleartext: true,
  },
  plugins: {
    SplashScreen: {
      launchShowDuration: 2000,
      backgroundColor: '#F3E5F5', // 【方案一】启动底色与品牌浅紫一致
      showSpinner: false,
    },
  },
};

export default config;
