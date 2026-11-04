<template>
  <div class="login-page">
    <div class="login-card">
      <h1 class="login-title">🐱 喵艺管理后台</h1>
      <p class="login-sub">仅管理员账号可登录</p>
      <el-form @keyup.enter="handleLogin">
        <el-form-item>
          <el-input v-model="username" placeholder="管理员账号" size="large" :prefix-icon="User" />
        </el-form-item>
        <el-form-item>
          <el-input v-model="password" type="password" placeholder="密码" size="large"
                    :prefix-icon="Lock" show-password />
        </el-form-item>
        <el-button class="login-btn" type="primary" size="large" :loading="loading" @click="handleLogin">
          登 录
        </el-button>
      </el-form>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { User, Lock } from '@element-plus/icons-vue'
import { login } from '../api'

const router = useRouter()
const username = ref('')
const password = ref('')
const loading = ref(false)

async function handleLogin() {
  if (!username.value || !password.value) {
    ElMessage.warning('请输入账号和密码')
    return
  }
  loading.value = true
  try {
    const { data: res } = await login(username.value, password.value)
    const d = res.data || {}
    if (d.role !== 'admin') {
      ElMessage.error('该账号不是管理员，无权访问')
      return
    }
    localStorage.setItem('admin_token', d.token || d.access_token)
    localStorage.setItem('admin_user', JSON.stringify({ username: d.username, role: d.role }))
    ElMessage.success('欢迎回来，管理员')
    router.push('/dashboard')
  } finally {
    loading.value = false
  }
}
</script>
