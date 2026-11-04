import axios from 'axios'
import { ElMessage } from 'element-plus'
import router from './router'

const api = axios.create({ baseURL: '/api', timeout: 15000 })

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('admin_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  (res) => {
    // 业务码非 200 时统一报错
    if (res.data && typeof res.data.code === 'number' && res.data.code !== 200) {
      ElMessage.error(res.data.message || '请求失败')
      return Promise.reject(new Error(res.data.message))
    }
    return res
  },
  (err) => {
    const status = err.response?.status
    if (status === 401 || status === 403) {
      localStorage.removeItem('admin_token')
      localStorage.removeItem('admin_user')
      ElMessage.error('登录已失效，请重新登录')
      router.push('/login')
    } else {
      ElMessage.error(err.response?.data?.detail || err.response?.data?.message || '网络错误')
    }
    return Promise.reject(err)
  }
)

// ========== 认证 ==========
export const login = (username, password) =>
  api.post('/auth/login', { username, password })

// ========== 看板 ==========
export const getDashboard = () => api.get('/admin/dashboard')

// ========== 用户管理 ==========
export const getUsers = (params) => api.get('/admin/users', { params })
export const updateUserRole = (userId, role) =>
  api.post(`/admin/users/${userId}/role?role=${role}`)
export const updateUserStatus = (userId, status) =>
  api.post(`/admin/users/${userId}/status?status=${status}`)
export const resetUserPassword = (userId, password) =>
  api.post(`/admin/users/${userId}/reset-password`, { password })
export const deleteUser = (userId) => api.delete(`/admin/users/${userId}`)

// ========== 消息管理 ==========
export const getMessages = (params) => api.get('/admin/messages', { params })

// ========== 反馈管理 ==========
export const getFeedback = (params) => api.get('/admin/feedback', { params })
export const deleteFeedback = (id) => api.delete(`/admin/feedback/${id}`)

// ========== 配置 ==========
export const getConfig = () => api.get('/admin/config')

export default api
