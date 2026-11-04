import { useState, useEffect } from 'react'
import { request } from '../utils/api'
import { Icon } from './icons'

interface User {
  id: number
  username: string
  phone: string
  role: string
  created_at: number
  status: string
}

interface UserDetail {
  id: number
  username: string
  phone: string
  email: string
  role: string
  created_at: number
  last_active: number
  message_count: number
  memory_count: number
}

interface Stats {
  total_users: number
  total_messages: number
  today_messages: number
  online_users: number
}

interface LlmPoolStatus {
  available: boolean
  providers: Array<{
    label: string
    provider: string
    model_name: string
    is_primary: boolean
    cooldown_remaining: number
  }>
  roles: Record<string, { model: string }>
  agents: Record<string, Record<string, unknown>>
}

interface AdminPanelProps {
  onClose: () => void
}

type TabType = 'users' | 'stats' | 'detail' | 'llm'

function formatDate(timestamp: number): string {
  if (!timestamp) return '从未'
  return new Date(timestamp * 1000).toLocaleString('zh-CN')
}

function formatPhone(phone: string): string {
  if (!phone) return '-'
  return phone.replace(/(\d{3})(\d{4})(\d{4})/, '$1****$3')
}

export function AdminPanel({ onClose }: AdminPanelProps) {
  const [activeTab, setActiveTab] = useState<TabType>('users')
  const [users, setUsers] = useState<User[]>([])
  const [stats, setStats] = useState<Stats | null>(null)
  const [selectedUser, setSelectedUser] = useState<UserDetail | null>(null)
  const [llmPool, setLlmPool] = useState<LlmPoolStatus | null>(null)
  const [isLoading, setIsLoading] = useState(false)

  useEffect(() => {
    loadUsers()
    loadStats()
  }, [])

  const loadUsers = async () => {
    setIsLoading(true)
    try {
      const result = await request('/admin/users')
      setUsers(result.data)
    } catch (error) {
      console.error('加载用户列表失败', error)
    } finally {
      setIsLoading(false)
    }
  }

  const loadStats = async () => {
    try {
      const result = await request('/admin/stats')
      setStats(result.data)
    } catch (error) {
      console.error('加载统计数据失败', error)
    }
  }

  const loadUserDetail = async (userId: number) => {
    setIsLoading(true)
    try {
      const result = await request(`/admin/user/${userId}`)
      setSelectedUser(result.data)
      setActiveTab('detail')
    } catch (error) {
      console.error('加载用户详情失败', error)
    } finally {
      setIsLoading(false)
    }
  }

  const loadLlmPool = async () => {
    setIsLoading(true)
    try {
      const result = await request('/admin/llm-pool')
      setLlmPool(result.data)
    } catch (error) {
      console.error('加载大脑状态失败', error)
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="admin-panel-overlay" onClick={onClose}>
      <div className="admin-panel" onClick={(e) => e.stopPropagation()}>
        <div className="admin-panel-header">
          <h2><Icon name="shield" size={19} /> 管理后台</h2>
          <button className="admin-panel-close" onClick={onClose} aria-label="关闭"><Icon name="x" size={18} /></button>
        </div>

        <div className="admin-panel-tabs">
          <button
            className={`admin-tab ${activeTab === 'users' ? 'active' : ''}`}
            onClick={() => { setActiveTab('users'); setSelectedUser(null) }}
          >
            用户列表
          </button>
          <button
            className={`admin-tab ${activeTab === 'stats' ? 'active' : ''}`}
            onClick={() => { setActiveTab('stats'); setSelectedUser(null) }}
          >
            系统统计
          </button>
          <button
            className={`admin-tab ${activeTab === 'llm' ? 'active' : ''}`}
            onClick={() => { setActiveTab('llm'); setSelectedUser(null); loadLlmPool() }}
          >
            <Icon name="brain" size={14} /> 大脑
          </button>
          {selectedUser && (
            <button
              className={`admin-tab ${activeTab === 'detail' ? 'active' : ''}`}
              onClick={() => setActiveTab('detail')}
            >
              用户详情
            </button>
          )}
        </div>

        <div className="admin-panel-content">
          {activeTab === 'users' && (
            <div className="admin-users">
              <div className="admin-table-header">
                <span>用户名</span>
                <span>手机号</span>
                <span>角色</span>
                <span>注册时间</span>
                <span>操作</span>
              </div>
              {isLoading ? (
                <div className="admin-loading">
                  <span className="cat-loading-paws">
                    <span><Icon name="paw" size={14} /></span><span><Icon name="paw" size={14} /></span><span><Icon name="paw" size={14} /></span><span><Icon name="paw" size={14} /></span>
                  </span>
                  <span className="cat-loading-text">喵爪翻阅中...</span>
                </div>
              ) : users.length === 0 ? (
                <div className="admin-empty">暂无用户</div>
              ) : (
                users.map((user) => (
                  <div key={user.id} className="admin-table-row">
                    <span>{user.username}</span>
                    <span>{formatPhone(user.phone)}</span>
                    <span className={`admin-role ${user.role}`}>{user.role === 'founder' ? '创始人' : user.role === 'admin' ? '管理员' : '普通用户'}</span>
                    <span>{formatDate(user.created_at)}</span>
                    <button
                      className="admin-detail-btn"
                      onClick={() => loadUserDetail(user.id)}
                    >
                      查看详情
                    </button>
                  </div>
                ))
              )}
            </div>
          )}

          {activeTab === 'stats' && stats && (
            <div className="admin-stats">
              <div className="stat-card">
                <div className="stat-icon"><Icon name="user" size={22} /></div>
                <div className="stat-value">{stats.total_users}</div>
                <div className="stat-label">总用户数</div>
              </div>
              <div className="stat-card">
                <div className="stat-icon"><Icon name="messageSquare" size={22} /></div>
                <div className="stat-value">{stats.total_messages}</div>
                <div className="stat-label">总消息数</div>
              </div>
              <div className="stat-card">
                <div className="stat-icon"><Icon name="calendar" size={22} /></div>
                <div className="stat-value">{stats.today_messages}</div>
                <div className="stat-label">今日消息数</div>
              </div>
              <div className="stat-card">
                <div className="stat-icon"><Icon name="activity" size={22} /></div>
                <div className="stat-value">{stats.online_users}</div>
                <div className="stat-label">在线用户数</div>
              </div>
            </div>
          )}

          {activeTab === 'llm' && (
            <div className="admin-llm-pool">
              {!llmPool || !llmPool.available ? (
                <div className="admin-loading"><span className="cat-loading-text">单供应商模式（未启用多智能体池）</span></div>
              ) : (
                <>
                  <h3 style={{ margin: '4px 0 10px', fontSize: 15, display: 'flex', alignItems: 'center', gap: 6 }}><Icon name="brain" size={15} /> 供应商池（按序故障转移）</h3>
                  {llmPool.providers.map((p, i) => (
                    <div key={i} className="memory-card" style={{ marginBottom: 8, padding: '10px 12px', display: 'flex', alignItems: 'center', gap: 10 }}>
                      <span style={{ display: 'inline-flex', alignItems: 'center', color: p.is_primary ? '#d8a13a' : '#8b5cf6' }}>
                        <Icon name={p.is_primary ? 'crown' : 'link'} size={18} />
                      </span>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 13, fontWeight: 700 }}>{p.label}{p.is_primary ? '（主力）' : ''}</div>
                        <div style={{ fontSize: 11, opacity: 0.7 }}>{p.provider}</div>
                      </div>
                      <span style={{ fontSize: 11, fontWeight: 700, color: p.cooldown_remaining > 0 ? '#e05a5a' : '#5aa46e' }}>
                        {p.cooldown_remaining > 0 ? `冷却 ${p.cooldown_remaining}s` : '正常'}
                      </span>
                    </div>
                  ))}
                  <h3 style={{ margin: '14px 0 10px', fontSize: 15, display: 'flex', alignItems: 'center', gap: 6 }}><Icon name="puzzle" size={15} /> 智能体分工（llm_apis.yaml）</h3>
                  {Object.entries(llmPool.roles).map(([role, cfg]) => (
                    <div key={role} className="memory-card" style={{ marginBottom: 6, padding: '8px 12px', display: 'flex', justifyContent: 'space-between' }}>
                      <span style={{ fontSize: 12.5, fontWeight: 700 }}>{role}</span>
                      <span style={{ fontSize: 12, opacity: 0.75 }}>{cfg.model}</span>
                    </div>
                  ))}
                  <button className="admin-back-btn" style={{ marginTop: 12, display: 'inline-flex', alignItems: 'center', gap: 6 }} onClick={loadLlmPool}><Icon name="refresh" size={15} /> 刷新状态</button>
                </>
              )}
            </div>
          )}

          {activeTab === 'detail' && selectedUser && (
            <div className="admin-detail">
              <button className="admin-back-btn" onClick={() => setActiveTab('users')} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                <Icon name="chevronLeft" size={15} /> 返回列表
              </button>
              <h3>{selectedUser.username} 的详细信息</h3>
              <div className="detail-grid">
                <div className="detail-item">
                  <span className="detail-label">ID</span>
                  <span className="detail-value">{selectedUser.id}</span>
                </div>
                <div className="detail-item">
                  <span className="detail-label">用户名</span>
                  <span className="detail-value">{selectedUser.username}</span>
                </div>
                <div className="detail-item">
                  <span className="detail-label">手机号</span>
                  <span className="detail-value">{formatPhone(selectedUser.phone)}</span>
                </div>
                <div className="detail-item">
                  <span className="detail-label">邮箱</span>
                  <span className="detail-value">{selectedUser.email || '-'}</span>
                </div>
                <div className="detail-item">
                  <span className="detail-label">角色</span>
                  <span className="detail-value">{selectedUser.role === 'founder' ? '创始人' : selectedUser.role === 'admin' ? '管理员' : '普通用户'}</span>
                </div>
                <div className="detail-item">
                  <span className="detail-label">注册时间</span>
                  <span className="detail-value">{formatDate(selectedUser.created_at)}</span>
                </div>
                <div className="detail-item">
                  <span className="detail-label">最后活跃</span>
                  <span className="detail-value">{formatDate(selectedUser.last_active)}</span>
                </div>
                <div className="detail-item">
                  <span className="detail-label">聊天记录数</span>
                  <span className="detail-value">{selectedUser.message_count}</span>
                </div>
                <div className="detail-item">
                  <span className="detail-label">记忆数</span>
                  <span className="detail-value">{selectedUser.memory_count}</span>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}