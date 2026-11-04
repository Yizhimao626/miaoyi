<template>
  <div>
    <div class="card">
      <div class="toolbar">
        <el-input v-model="search" placeholder="搜索用户名/手机号，回车搜索" clearable style="width: 260px"
                  :prefix-icon="Search" @keyup.enter="onSearch" @clear="onSearch" />
        <span style="color: #90919e; font-size: 13px;">共 {{ total }} 个用户</span>
      </div>

      <el-table :data="users" stripe style="width: 100%">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="username" label="用户名" min-width="140" show-overflow-tooltip />
        <el-table-column prop="phone" label="手机号" width="130" />
        <el-table-column prop="role" label="角色" width="90">
          <template #default="{ row }">
            <el-tag :type="row.role === 'admin' ? 'danger' : 'primary'" size="small">
              {{ row.role === 'admin' ? '管理员' : '用户' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.status === 'active' ? 'success' : 'info'" size="small">
              {{ row.status === 'active' ? '正常' : '禁用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="注册时间" width="165">
          <template #default="{ row }">{{ fmtTs(row.created_ts) }}</template>
        </el-table-column>
        <el-table-column label="最后登录" width="165">
          <template #default="{ row }">{{ fmtTs(row.last_login_ts) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="300" fixed="right">
          <template #default="{ row }">
            <el-button size="small" @click="toggleStatus(row)">{{ row.status === 'active' ? '禁用' : '启用' }}</el-button>
            <el-button size="small" :type="row.role === 'admin' ? 'warning' : 'success'" @click="toggleRole(row)">
              {{ row.role === 'admin' ? '降为用户' : '设为管理员' }}
            </el-button>
            <el-button size="small" type="primary" plain @click="openResetPwd(row)">重置密码</el-button>
            <el-popconfirm title="确定删除该用户？此操作不可恢复！" width="230" @confirm="doDelete(row)">
              <template #reference>
                <el-button size="small" type="danger" plain>删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>

    <!-- 分页 -->
    <div style="display: flex; justify-content: flex-end; margin-top: 14px;">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[10, 20, 50, 100]"
        layout="total, sizes, prev, pager, next, jumper"
        background
        @current-change="load"
        @size-change="onSizeChange"
      />
    </div>
    </div>

    <!-- 重置密码弹窗 -->
    <el-dialog v-model="pwdVisible" title="重置用户密码" width="420">
      <el-alert type="warning" :closable="false" style="margin-bottom: 14px"
                :title="`将为用户「${pwdUser?.username}」设置新密码`" />
      <el-input v-model="newPwd" placeholder="新密码（至少 8 位）" show-password />
      <template #footer>
        <el-button @click="pwdVisible = false">取消</el-button>
        <el-button type="primary" :loading="pwdLoading" @click="doResetPwd">确认重置</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { getUsers, updateUserRole, updateUserStatus, resetUserPassword, deleteUser } from '../api'

const users = ref([])
const search = ref('')
const page = ref(1)
const pageSize = ref(10)
const total = ref(0)

function fmtTs(ts) {
  if (!ts) return '-'
  const d = new Date(ts * 1000)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

async function load() {
  const { data: res } = await getUsers({
    page: page.value,
    page_size: pageSize.value,
    search: search.value.trim(),
  })
  const payload = res.data || {}
  users.value = payload.users || []
  total.value = payload.total || 0
  // 当前页被删空时回退一页
  if (users.value.length === 0 && page.value > 1) {
    page.value -= 1
    await load()
  }
}

function onSearch() {
  page.value = 1
  load()
}

function onSizeChange() {
  page.value = 1
  load()
}

async function toggleStatus(row) {
  const next = row.status === 'active' ? 'disabled' : 'active'
  await updateUserStatus(row.id, next)
  ElMessage.success(next === 'disabled' ? '已禁用' : '已启用')
  load()
}

async function toggleRole(row) {
  const next = row.role === 'admin' ? 'user' : 'admin'
  await ElMessageBox.confirm(
    `确定将「${row.username}」${next === 'admin' ? '设为管理员' : '降为普通用户'}？`,
    '修改角色', { type: 'warning' })
  await updateUserRole(row.id, next)
  ElMessage.success('角色已更新')
  load()
}

const pwdVisible = ref(false)
const pwdUser = ref(null)
const newPwd = ref('')
const pwdLoading = ref(false)
function openResetPwd(row) {
  pwdUser.value = row
  newPwd.value = ''
  pwdVisible.value = true
}
async function doResetPwd() {
  if (newPwd.value.length < 8) {
    ElMessage.warning('密码长度至少 8 位')
    return
  }
  pwdLoading.value = true
  try {
    await resetUserPassword(pwdUser.value.id, newPwd.value)
    ElMessage.success('密码已重置')
    pwdVisible.value = false
  } finally {
    pwdLoading.value = false
  }
}

async function doDelete(row) {
  await deleteUser(row.id)
  ElMessage.success('用户已删除')
  load()
}

onMounted(load)
</script>
