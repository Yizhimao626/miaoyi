<template>
  <div class="card">
    <el-table v-loading="loading" :data="items" stripe style="width: 100%">
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="user_id" label="用户ID" width="110" />
      <el-table-column prop="content" label="反馈内容" min-width="400" show-overflow-tooltip />
      <el-table-column label="提交时间" width="165">
        <template #default="{ row }">{{ fmtTs(row.created_ts) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="100" fixed="right">
        <template #default="{ row }">
          <el-popconfirm title="确定删除该反馈？" width="200" @confirm="doDelete(row)">
            <template #reference>
              <el-button size="small" type="danger" plain>删除</el-button>
            </template>
          </el-popconfirm>
        </template>
      </el-table-column>
    </el-table>

    <div class="pager">
      <el-pagination background layout="total, prev, pager, next"
                     :total="total" :current-page="page" :page-size="pageSize"
                     @current-change="(p) => load(p)" />
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getFeedback, deleteFeedback } from '../api'

const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const loading = ref(false)

function fmtTs(ts) {
  if (!ts) return '-'
  const d = new Date(ts * 1000)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

async function load(p = page.value) {
  page.value = p
  loading.value = true
  try {
    const { data: res } = await getFeedback({ page: p, page_size: pageSize })
    items.value = res.data?.items || []
    total.value = res.data?.total || 0
  } finally {
    loading.value = false
  }
}

async function doDelete(row) {
  await deleteFeedback(row.id)
  ElMessage.success('已删除')
  load()
}

onMounted(() => load(1))
</script>
