<template>
  <div class="card" v-loading="loading">
    <el-alert type="info" :closable="false" style="margin-bottom: 16px"
              title="敏感字段已脱敏显示（***）。此页面为只读视图，修改请直接编辑服务器上的 config.yaml。" />
    <el-input v-model="filter" placeholder="筛选配置项" clearable style="width: 260px; margin-bottom: 14px" />
    <pre class="config-view">{{ prettyFiltered }}</pre>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { getConfig } from '../api'

const config = ref({})
const filter = ref('')
const loading = ref(false)

const prettyFiltered = computed(() => {
  const kw = filter.value.trim().toLowerCase()
  const walk = (obj, path) => {
    const out = {}
    for (const [k, v] of Object.entries(obj || {})) {
      const p = path ? `${path}.${k}` : k
      if (kw && !p.toLowerCase().includes(kw)) continue
      if (v && typeof v === 'object' && !Array.isArray(v)) out[k] = walk(v, p)
      else out[k] = v
    }
    return out
  }
  return JSON.stringify(walk(config.value, ''), null, 2)
})

onMounted(async () => {
  loading.value = true
  try {
    const { data: res } = await getConfig()
    config.value = res.data || {}
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.config-view {
  background: #f8f8fc;
  border: 1px solid #ece9f8;
  border-radius: 10px;
  padding: 16px;
  font-size: 12.5px;
  line-height: 1.7;
  max-height: calc(100vh - 260px);
  overflow: auto;
  margin: 0;
}
</style>
