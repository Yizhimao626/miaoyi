<template>
  <div v-loading="loading">
    <!-- 核心统计卡片 -->
    <div class="stat-grid">
      <div v-for="s in statCards" :key="s.label" class="stat-card">
        <div class="stat-icon" :style="{ background: s.color }">{{ s.icon }}</div>
        <div>
          <div class="stat-value">{{ s.value }}</div>
          <div class="stat-label">{{ s.label }}</div>
        </div>
      </div>
    </div>

    <div style="display: grid; grid-template-columns: minmax(0, 2fr) minmax(0, 1fr); gap: 16px; align-items: start;">
      <!-- 7天消息趋势 -->
      <div class="card">
        <h3 style="margin: 0 0 8px; font-size: 15px; color: #333;">近 7 天消息趋势</h3>
        <div class="trend-chart">
          <div v-for="t in trend" :key="t.ts" class="trend-col">
            <span class="trend-num">{{ t.count }}</span>
            <div class="trend-bar" :style="{ height: barHeight(t.count) + '%' }" />
            <span class="trend-date">{{ fmtDate(t.ts) }}</span>
          </div>
        </div>
      </div>

      <!-- 系统状态 -->
      <div class="card">
        <h3 style="margin: 0 0 14px; font-size: 15px; color: #333;">系统状态</h3>
        <div style="display: flex; flex-direction: column; gap: 12px; font-size: 14px;">
          <div v-for="row in sysRows" :key="row.label" style="display: flex; justify-content: space-between; align-items: center; gap: 12px;">
            <span style="color: #90919e; white-space: nowrap;">{{ row.label }}</span>
            <b v-if="!row.tag" style="text-align: right;">{{ row.value }}</b>
            <el-tag v-else size="small" :type="dbType === 'mysql' ? 'success' : 'info'">{{ row.value }}</el-tag>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { getDashboard } from '../api'

const loading = ref(false)
const dashboard = ref({})

const data = computed(() => dashboard.value.counts || {})
const sys = computed(() => dashboard.value.system || {})
const dbType = computed(() => dashboard.value.db_type || 'sqlite')
const trend = computed(() => dashboard.value.trend_7d || [])

const statCards = computed(() => [
  { label: '注册用户', value: data.value.users ?? 0, icon: '👥', color: 'linear-gradient(135deg,#7c5cf0,#a78bfa)' },
  { label: '消息总数', value: data.value.messages ?? 0, icon: '💬', color: 'linear-gradient(135deg,#36cfc9,#5cdbd3)' },
  { label: '会话数', value: data.value.conversations ?? 0, icon: '📂', color: 'linear-gradient(135deg,#f759ab,#ff85c0)' },
  { label: '语义记忆', value: data.value.memories ?? 0, icon: '🧠', color: 'linear-gradient(135deg,#ffc53d,#ffd666)' },
  { label: '情感记忆', value: data.value.emotional_memories ?? 0, icon: '💗', color: 'linear-gradient(135deg,#ff4d4f,#ff7875)' },
  { label: '用户反馈', value: data.value.feedback ?? 0, icon: '📮', color: 'linear-gradient(135deg,#597ef7,#85a5ff)' },
  { label: '通话记录', value: data.value.call_history ?? 0, icon: '📞', color: 'linear-gradient(135deg,#73d13d,#95de64)' },
])

const maxTrend = computed(() => Math.max(1, ...trend.value.map(t => t.count)))
function barHeight(count) { return Math.round((count / maxTrend.value) * 85) + 8 }
function fmtDate(ts) {
  const d = new Date(ts * 1000)
  return `${d.getMonth() + 1}/${d.getDate()}`
}

const sysRows = computed(() => [
  { label: '数据库类型', value: dbType.value.toUpperCase(), tag: true },
  { label: '今日消息', value: dashboard.value.today_messages ?? '-' },
  { label: '24h 活跃会话', value: dashboard.value.active_sessions_24h ?? '-' },
  { label: '禁用用户数', value: dashboard.value.disabled_users ?? '-' },
  { label: '内存占用', value: `${sys.value.memory_usage_mb ?? '-'} MB` },
  { label: 'CPU', value: `${sys.value.cpu_percent ?? '-'}%` },
  { label: '线程数', value: sys.value.threads ?? '-' },
])

onMounted(async () => {
  loading.value = true
  try {
    const { data: res } = await getDashboard()
    dashboard.value = res.data || {}
  } finally {
    loading.value = false
  }
})
</script>
