<template>
  <div class="card">
    <div class="toolbar">
      <el-input v-model="keyword" placeholder="搜索消息内容 / 会话ID" clearable style="width: 280px"
                :prefix-icon="Search" @keyup.enter="load(1)" @clear="load(1)" />
      <div style="display: flex; gap: 8px;">
        <el-button type="primary" @click="load(1)">搜索</el-button>
        <el-button @click="keyword = ''; load(1)">重置</el-button>
      </div>
    </div>

    <el-table v-loading="loading" :data="items" stripe style="width: 100%">
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="role" label="角色" width="90">
        <template #default="{ row }">
          <el-tag :type="row.role === 'user' ? 'primary' : 'success'" size="small">
            {{ row.role === 'user' ? '用户' : '喵艺' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="text" label="内容" min-width="300" show-overflow-tooltip />
      <el-table-column prop="session_id" label="会话" width="150" show-overflow-tooltip>
        <template #default="{ row }"><span class="mono">{{ row.session_id }}</span></template>
      </el-table-column>
      <el-table-column prop="emotion" label="情感" width="100">
        <template #default="{ row }">{{ row.emotion || '-' }}</template>
      </el-table-column>
      <el-table-column label="响应耗时" width="100">
        <template #default="{ row }">{{ row.response_time_ms ? row.response_time_ms + 'ms' : '-' }}</template>
      </el-table-column>
      <el-table-column label="时间" width="165">
        <template #default="{ row }">{{ fmtTs(row.created_ts) }}</template>
      </el-table-column>
    </el-table>

    <div class="pager">
      <el-pagination background layout="total, prev, pager, next, sizes"
                     :total="total" :current-page="page" :page-size="pageSize"
                     :page-sizes="[10, 20, 50, 100]"
                     @current-change="(p) => load(p)" @size-change="(s) => { pageSize = s; load(1) }" />
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { Search } from '@element-plus/icons-vue'
import { getMessages } from '../api'

const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const keyword = ref('')
const loading = ref(false)

function fmtTs(ts) {
  if (!ts) return '-'
  const d = new Date(ts * 1000)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

async function load(p = page.value) {
  page.value = p
  loading.value = true
  try {
    const { data: res } = await getMessages({
      page: p, page_size: pageSize.value, keyword: keyword.value.trim(),
    })
    items.value = res.data?.items || []
    total.value = res.data?.total || 0
  } finally {
    loading.value = false
  }
}

onMounted(() => load(1))
</script>
