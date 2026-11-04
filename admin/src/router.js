import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/login', name: 'Login', component: () => import('./views/Login.vue') },
  {
    path: '/',
    component: () => import('./views/Layout.vue'),
    redirect: '/dashboard',
    children: [
      { path: 'dashboard', name: 'Dashboard', component: () => import('./views/Dashboard.vue'), meta: { title: '数据看板', icon: 'DataBoard' } },
      { path: 'users', name: 'Users', component: () => import('./views/Users.vue'), meta: { title: '用户管理', icon: 'User' } },
      { path: 'messages', name: 'Messages', component: () => import('./views/Messages.vue'), meta: { title: '消息管理', icon: 'ChatDotRound' } },
      { path: 'feedback', name: 'Feedback', component: () => import('./views/Feedback.vue'), meta: { title: '用户反馈', icon: 'Comment' } },
      { path: 'config', name: 'Config', component: () => import('./views/Config.vue'), meta: { title: '系统配置', icon: 'Setting' } },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/dashboard' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 路由守卫：未登录跳转登录页
router.beforeEach((to) => {
  const token = localStorage.getItem('admin_token')
  if (to.name !== 'Login' && !token) return { name: 'Login' }
  if (to.name === 'Login' && token) return { name: 'Dashboard' }
  return true
})

export default router
