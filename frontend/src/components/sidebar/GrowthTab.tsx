import type { GrowthData, EmotionalState } from '../../types'
import { EmotionIndicator } from '../EmotionComponents'
import { Icon } from '../icons'

interface RelationshipStatus {
  stage: string
  days: number
  milestone: string
}

interface Anniversary {
  next_date: string
  days_left: number
}

interface GrowthTabProps {
  growthData: GrowthData
  emotionalState: EmotionalState
  relationshipStatus: RelationshipStatus | null
  anniversary: Anniversary | null
}

export function GrowthTab({ growthData, emotionalState, relationshipStatus, anniversary }: GrowthTabProps) {
  const happy = emotionalState.happy ?? 0
  const sad = emotionalState.sad ?? 0
  const angry = emotionalState.angry ?? 0
  const love = emotionalState.love ?? 0

  return (
    <div className="growth-panel">
      {/* 【第4步】情绪维度指示器：从聊天页中间状态行迁入 */}
      <div className="growth-emotion-indicator">
        <EmotionIndicator
          valence={emotionalState.valence}
          arousal={emotionalState.arousal}
          intimacy={emotionalState.intimacy}
          trust={emotionalState.trust}
        />
      </div>
      <div className="growth-item">
        <span className="growth-label">等级</span>
        <span className="growth-value highlight">Lv.{growthData.level}</span>
      </div>
      <div className="growth-item">
        <span className="growth-label">关系</span>
        <span className="growth-value">{growthData.intimacyStage}</span>
      </div>
      {relationshipStatus && (
        <>
          <div className="growth-item">
            <span className="growth-label">认识天数</span>
            <span className="growth-value"><Icon name="star" size={13} /> {relationshipStatus.days} 天</span>
          </div>
          {relationshipStatus.milestone && (
            <div className="growth-item">
              <span className="growth-label">下一个里程碑</span>
              <span className="growth-value">{relationshipStatus.milestone}</span>
            </div>
          )}
        </>
      )}
      {anniversary && anniversary.days_left !== undefined && (
        <div className="growth-item">
          <span className="growth-label">纪念日</span>
          <span className="growth-value"><Icon name="sparkles" size={13} /> {anniversary.days_left} 天后</span>
        </div>
      )}
      <div className="settings-divider" />
      <div className="growth-item">
        <span className="growth-label">经验值</span>
        <span className="growth-value">{growthData.exp}/{growthData.expMax}</span>
      </div>
      <div className="growth-bar-bg">
        <div className="growth-bar-fill" style={{ width: `${(growthData.exp / growthData.expMax) * 100}%` }} />
      </div>
      <div className="settings-divider" />
      <div className="growth-item">
        <span className="growth-label">亲密度</span>
        <span className="growth-value">{growthData.intimacy}%</span>
      </div>
      <div className="growth-bar-bg">
        <div className="growth-bar-fill love" style={{ width: `${growthData.intimacy}%` }} />
      </div>
      <div className="settings-divider" />
      <div className="growth-item">
        <span className="growth-label">开心</span>
        <span className="growth-value">{happy}%</span>
      </div>
      <div className="growth-bar-bg">
        <div className="growth-bar-fill happy" style={{ width: `${happy}%` }} />
      </div>
      <div className="growth-item">
        <span className="growth-label">难过</span>
        <span className="growth-value">{sad}%</span>
      </div>
      <div className="growth-bar-bg">
        <div className="growth-bar-fill sad" style={{ width: `${sad}%` }} />
      </div>
      <div className="growth-item">
        <span className="growth-label">生气</span>
        <span className="growth-value">{angry}%</span>
      </div>
      <div className="growth-bar-bg">
        <div className="growth-bar-fill angry" style={{ width: `${angry}%` }} />
      </div>
      <div className="growth-item">
        <span className="growth-label">喜爱</span>
        <span className="growth-value">{love}%</span>
      </div>
      <div className="growth-bar-bg">
        <div className="growth-bar-fill love" style={{ width: `${love}%` }} />
      </div>
    </div>
  )
}