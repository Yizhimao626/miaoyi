import { useState, useEffect } from 'react'
import { getDiaryToday, getDiaryList, getDiaryMemoryLane, getMoments, toggleMomentLike } from '../../utils/api'
import { Icon, type IconName } from '../icons'

/**
 * 【L6 猫咪日记 + 回忆相册】【L7 朋友圈动态】侧边栏日记页
 * - 朋友圈：喵艺基于作息表发布的"此刻在做什么"动态，可点赞
 * - 今日日记：AI 第一人称（与签到/睡眠整理打通，当日对话≥4条才生成）
 * - 那年今日：历史同月同日日记
 * - 纪念卡片：关系里程碑 + 纪念日 + 连续签到
 * - 回忆相册：历史日记列表
 */
interface DiaryEntry {
  id: number
  diary_date: string
  diary_text: string
  emotion_summary: string
}

interface Milestone {
  type: string
  description: string
  ts: number
}

interface MemoryLane {
  year_ago_diaries: DiaryEntry[]
  milestones: Milestone[]
  anniversary: {
    days_together?: number
    next_milestone?: number
    days_to_next_milestone?: number
    next_milestone_description?: string
    today_milestone?: number
    today_milestone_message?: string
  } | null
  checkin: { streak: number; checked_today: boolean } | null
}

interface DiaryTabProps {
  onShowToast: (message: string, type?: 'success' | 'error' | 'warning') => void
}

/** 【L7 朋友圈】喵艺的生活动态 */
interface CatMoment {
  id: number
  content: string
  mood_emoji: string
  activity: string
  liked: boolean
  created_ts: number
}

const MILESTONE_ICONS: Record<string, IconName> = {
  stage_up: 'gift',
  anniversary: 'sparkles',
}

export function DiaryTab({ onShowToast }: DiaryTabProps) {
  const [todayDiary, setTodayDiary] = useState<string | null>(null)
  const [todayReason, setTodayReason] = useState<string>('')
  const [todayEmotion, setTodayEmotion] = useState<string>('')
  const [generating, setGenerating] = useState(false)
  const [diaries, setDiaries] = useState<DiaryEntry[]>([])
  const [lane, setLane] = useState<MemoryLane | null>(null)
  const [moments, setMoments] = useState<CatMoment[]>([])

  useEffect(() => {
    loadAll()
  }, [])

  const loadAll = async () => {
    try {
      const [todayRes, listRes, laneRes, momentsRes] = await Promise.all([
        getDiaryToday(),
        getDiaryList(60),
        getDiaryMemoryLane(),
        getMoments(10),
      ])
      const todayData = todayRes.data || {}
      setTodayDiary(todayData.diary || null)
      setTodayReason(todayData.reason || '')
      setTodayEmotion(todayData.emotion_summary || '')
      setDiaries(listRes.data?.diaries || [])
      setLane(laneRes.data || null)
      setMoments(momentsRes.data?.moments || [])
    } catch {
      onShowToast('加载日记失败', 'error')
    }
  }

  /** 【L7 朋友圈】点赞/取消点赞，乐观更新 */
  const handleLikeMoment = async (m: CatMoment) => {
    const next = !m.liked
    setMoments((prev) => prev.map((x) => (x.id === m.id ? { ...x, liked: next } : x)))
    try {
      await toggleMomentLike(m.id, next)
      onShowToast(next ? '喵艺收到了你的喜欢～' : '已取消点赞', 'success')
    } catch {
      setMoments((prev) => prev.map((x) => (x.id === m.id ? { ...x, liked: !next } : x)))
      onShowToast('点赞失败', 'error')
    }
  }

  /** 动态发布时间的相对描述 */
  const formatMomentTime = (ts: number) => {
    const diff = Date.now() / 1000 - ts
    if (diff < 60) return '刚刚'
    if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
    if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
    return new Date(ts * 1000).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
  }

  const handleRegenerate = async () => {
    // 今日已生成时不重复生成；手动刷新即重新拉取（后端幂等）
    setGenerating(true)
    try {
      const res = await getDiaryToday()
      const d = res.data?.diary || null
      setTodayDiary(d)
      setTodayReason(res.data?.reason || '')
      setTodayEmotion(res.data?.emotion_summary || '')
      if (d) onShowToast('喵艺的日记更新啦～', 'success')
      else if (res.data?.reason === 'not_enough_material') onShowToast('今天聊得还不够多，喵艺稍后再写', 'warning')
      else onShowToast('喵艺还没写好，稍后再来看看吧', 'warning')
    } catch {
      onShowToast('生成日记失败', 'error')
    } finally {
      setGenerating(false)
    }
  }

  return (
    <div className="growth-panel diary-panel">
      {/* 【L7 朋友圈】喵艺的生活动态 */}
      <div className="growth-item">
        <span className="growth-label"><Icon name="image" size={13} /> 喵艺的朋友圈</span>
        <span className="growth-value" style={{ fontSize: 12 }}>{moments.length} 条</span>
      </div>
      {moments.map((m) => (
        <div key={`cm-${m.id}`} className="diary-moment-card" style={{
          borderRadius: 12, padding: '10px 14px', margin: '6px 0',
          background: 'rgba(139,92,246,0.08)',
        }}>
          <div className="growth-item">
            <span className="growth-label" style={{ fontSize: 13 }}>
              {m.mood_emoji} {m.content}
            </span>
            <span style={{ fontSize: 10, opacity: 0.5, flexShrink: 0 }}>{formatMomentTime(m.created_ts)}</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 4 }}>
            <span style={{ fontSize: 11, opacity: 0.55 }}>正在 {m.activity}</span>
            <button
              className="btn-press"
              onClick={() => handleLikeMoment(m)}
              style={{
                fontSize: 12, padding: '2px 10px', borderRadius: 10, border: 'none',
                background: m.liked ? 'rgba(244,114,182,0.18)' : 'transparent',
                color: m.liked ? '#f472b6' : 'inherit', opacity: m.liked ? 1 : 0.55,
                cursor: 'pointer',
              }}
            >
              {m.liked ? <><Icon name="heart" size={12} /> 已喜欢</> : <><Icon name="paw" size={12} /> 喜欢</>}
            </button>
          </div>
        </div>
      ))}
      {moments.length === 0 && (
        <p style={{ fontSize: 12, opacity: 0.6, textAlign: 'center', margin: '6px 0 12px' }}>
          喵艺还没发过动态，等它玩累了就会来分享啦～
        </p>
      )}

      {/* 今日日记 */}
      <div className="diary-card" style={{
        background: 'linear-gradient(135deg, rgba(139,92,246,0.12), rgba(244,114,182,0.10))',
        borderRadius: 12, padding: '12px 14px', marginBottom: 10,
      }}>
        <div className="growth-item">
          <span className="growth-label"><Icon name="book" size={13} /> 今日日记</span>
          <span className="growth-value" style={{ fontSize: 12 }}>
            {new Date().toLocaleDateString('zh-CN', { month: 'long', day: 'numeric' })}
          </span>
        </div>
        {todayDiary ? (
          <>
            <p style={{ margin: '8px 0', fontSize: 13, lineHeight: 1.7, whiteSpace: 'pre-wrap' }}>
              {todayDiary}
            </p>
            {todayEmotion && (
              <span style={{ fontSize: 11, opacity: 0.65 }}><Icon name="paw" size={11} /> {todayEmotion}</span>
            )}
          </>
        ) : (
          <p style={{ margin: '8px 0', fontSize: 13, opacity: 0.7 }}>
            {generating
              ? '喵艺正在翻今天的记忆，写日记中…'
              : todayReason === 'not_enough_material'
                ? '今天和喵艺聊得还不够多，聊一会儿再来写日记吧～'
                : '喵艺今天的日记还没写好，稍后再来看看吧～'}
          </p>
        )}
        <button
          className="btn-press"
          onClick={handleRegenerate}
          disabled={generating}
          style={{
            marginTop: 8, fontSize: 12, padding: '6px 12px', borderRadius: 8,
            border: '1px solid rgba(139,92,246,0.35)', background: 'rgba(139,92,246,0.15)',
            cursor: generating ? 'wait' : 'pointer',
          }}
        >
          {generating
            ? '写日记中…'
            : todayDiary
              ? <><Icon name="refresh" size={12} /> 刷新日记</>
              : <><Icon name="edit" size={12} /> 提醒喵艺写日记</>}
        </button>
      </div>

      {/* 那年今日 */}
      {lane && lane.year_ago_diaries.length > 0 && (
        <div className="settings-divider" />
      )}
      {lane && lane.year_ago_diaries.map((d) => (
        <div key={`yl-${d.id}`} className="diary-card" style={{
          background: 'rgba(255,190,120,0.10)', borderRadius: 12,
          padding: '10px 14px', margin: '10px 0',
        }}>
          <div className="growth-item">
            <span className="growth-label"><Icon name="clock" size={13} /> 那年今日 · {d.diary_date}</span>
          </div>
          <p style={{ margin: '6px 0 0', fontSize: 12, lineHeight: 1.7, opacity: 0.85, whiteSpace: 'pre-wrap' }}>
            {d.diary_text}
          </p>
        </div>
      ))}

      {/* 纪念卡片 */}
      {lane && (
        <div className="diary-cards-row" style={{ display: 'flex', gap: 8, margin: '10px 0', flexWrap: 'wrap' }}>
          {lane.checkin && (
            <div className="diary-memorial-card" style={{
              flex: '1 1 40%', background: 'rgba(250,204,21,0.10)', borderRadius: 12, padding: '8px 12px',
            }}>
              <div style={{ fontSize: 12, opacity: 0.75 }}><Icon name="paw" size={11} /> 连续签到</div>
              <div style={{ fontSize: 18, fontWeight: 600 }}>
                {lane.checkin.streak} <span style={{ fontSize: 11, fontWeight: 400 }}>天</span>
              </div>
              <div style={{ fontSize: 10, opacity: 0.6 }}>
                {lane.checkin.checked_today ? '今天已签到' : '今天还没签到哦'}
              </div>
            </div>
          )}
          {lane.anniversary && lane.anniversary.days_together != null && (
            <div className="diary-memorial-card" style={{
              flex: '1 1 40%', background: 'rgba(244,114,182,0.10)', borderRadius: 12, padding: '8px 12px',
            }}>
              <div style={{ fontSize: 12, opacity: 0.75 }}><Icon name="heart" size={11} /> 相识</div>
              <div style={{ fontSize: 18, fontWeight: 600 }}>
                {lane.anniversary.days_together} <span style={{ fontSize: 11, fontWeight: 400 }}>天</span>
              </div>
              <div style={{ fontSize: 10, opacity: 0.6 }}>
                {lane.anniversary.next_milestone
                  ? `距${lane.anniversary.next_milestone_description || `${lane.anniversary.next_milestone}天纪念`}还有 ${lane.anniversary.days_to_next_milestone} 天`
                  : '已达成所有里程碑'}
              </div>
            </div>
          )}
        </div>
      )}
      {lane && lane.milestones.length > 0 && (
        <div className="diary-milestones">
          {lane.milestones.slice(-5).reverse().map((m, i) => (
            <div key={`ms-${i}`} className="growth-item">
              <span className="growth-label">
                <Icon name={MILESTONE_ICONS[m.type] || 'star'} size={12} /> {new Date(m.ts * 1000).toLocaleDateString('zh-CN')}
              </span>
              <span className="growth-value" style={{ fontSize: 12 }}>{m.description}</span>
            </div>
          ))}
        </div>
      )}

      {/* 回忆相册 */}
      <div className="settings-divider" />
      <div className="growth-item">
        <span className="growth-label"><Icon name="folder" size={13} /> 回忆相册</span>
        <span className="growth-value" style={{ fontSize: 12 }}>{diaries.length} 篇</span>
      </div>
      {diaries.map((d) => (
        <details key={d.id} className="diary-archive-item" style={{
          borderRadius: 10, padding: '8px 12px', margin: '6px 0',
          background: 'rgba(148,163,184,0.08)',
        }}>
          <summary style={{ cursor: 'pointer', fontSize: 13 }}>
            <Icon name="calendar" size={12} /> {d.diary_date}
            {d.emotion_summary && <span style={{ fontSize: 11, opacity: 0.6, marginLeft: 8 }}>{d.emotion_summary}</span>}
          </summary>
          <p style={{ margin: '8px 0 2px', fontSize: 12, lineHeight: 1.7, whiteSpace: 'pre-wrap', opacity: 0.85 }}>
            {d.diary_text}
          </p>
        </details>
      ))}
      {diaries.length === 0 && (
        <p style={{ fontSize: 12, opacity: 0.6, textAlign: 'center', margin: '12px 0' }}>
          相册还空空的，和喵艺聊聊天，明天这里就会有多彩的回忆啦～
        </p>
      )}
    </div>
  )
}
