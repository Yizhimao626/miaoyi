<template>
  <div class="layout">
    <aside class="sidebar">
      <div class="logo"><span class="cat">🐱</span>喵艺管理后台</div>
      <nav class="menu">
        <div v-for="item in menus" :key="item.path" class="menu-item"
             :class="{ active: $route.path === item.path }" @click="$router.push(item.path)">
          <el-icon><component :is="item.icon" /></el-icon>
          <span>{{ item.title }}</span>
        </div>
      </nav>
    </aside>
    <div class="main">
      <header class="header">
        <div class="title">{{ $route.meta.title || '管理后台' }}</div>
        <div class="admin-info">
          <el-tag type="warning" effect="plain" size="small">管理员</el-tag>
          <span>{{ adminName }}</span>
          <el-button link type="danger" @click="logout">退出</el-button>
        </div>
      </header>
      <main class="content">
        <router-view />
      </main>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { DataBoard, User, ChatDotRound, Comment, Setting } from '@element-plus/icons-vue'

const router = useRouter()
const menus = [
  { path: '/dashboard', title: '数据看板', icon: DataBoard },
  { path: '/users', title: '用户管理', icon: User },
  { path: '/messages', title: '消息管理', icon: ChatDotRound },
  { path: '/feedback', title: '用户反馈', icon: Comment },
  { path: '/config', title: '系统配置', icon: Setting },
]
const adminName = computed(() => {
  try { return JSON.parse(localStorage.getItem('admin_user') || '{}').username || 'admin' }
  catch { return 'admin' }
})

function logout() {
  localStorage.removeItem('admin_token')
  localStorage.removeItem('admin_user')
  router.push('/login')
}
</script>
