import type { GrowthData, UserProfile } from '../../types'
import { Icon } from '../icons'

interface ProfileTabProps {
  growthData: GrowthData
  userProfile: UserProfile | null
  onCheckin: () => void
  onNavigate: (tab: string) => void
  onLogout: () => void
}

export function ProfileTab({ growthData, userProfile, onCheckin, onNavigate, onLogout }: ProfileTabProps) {
  const isStaff = userProfile?.role === 'founder' || userProfile?.role === 'admin'
  const roleLabel = userProfile?.role === 'founder' ? '创始人' : userProfile?.role === 'admin' ? '管理员' : '普通用户'
  return (
    <div className="profile-panel">
      <div className="profile-header">
        <div className="profile-avatar">
          {userProfile?.nickname?.charAt(0) || userProfile?.username?.charAt(0) || <Icon name="user" size={26} />}
        </div>
        <div className="profile-info">
          <div className="profile-name">{userProfile?.nickname || userProfile?.username || '用户'}</div>
          <div className="profile-role">
            <Icon name={isStaff ? 'crown' : 'cat'} size={13} /> {roleLabel}
          </div>
        </div>
      </div>
      <div className="settings-divider" />
      <div className="profile-stats">
        <div className="profile-stat-item">
          <span className="profile-stat-value highlight">Lv.{growthData.level}</span>
          <span className="profile-stat-label">等级</span>
        </div>
        <div className="profile-stat-item">
          <span className="profile-stat-value">{growthData.intimacy}%</span>
          <span className="profile-stat-label">亲密度</span>
        </div>
        <div className="profile-stat-item">
          <span className="profile-stat-value">{growthData.exp}/{growthData.expMax}</span>
          <span className="profile-stat-label">经验</span>
        </div>
      </div>
      <div className="settings-divider" />
      <button className="settings-action-btn checkin btn-press" onClick={onCheckin}>
        <Icon name="bell" size={16} /> 每日签到
      </button>
      <div className="settings-divider" />
      <button className="settings-action-btn btn-press" onClick={() => onNavigate('memory')}>
        <Icon name="brain" size={16} /> 我的记忆
      </button>
      <button className="settings-action-btn btn-press" onClick={() => onNavigate('growth')}>
        <Icon name="trendingUp" size={16} /> 成长历程
      </button>
      <div className="settings-divider" />
      <button className="settings-action-btn danger btn-press" onClick={onLogout}>
        <Icon name="logout" size={16} /> 退出登录
      </button>
    </div>
  )
}