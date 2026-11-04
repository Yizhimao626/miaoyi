import { useState, useEffect } from 'react'
import type { EmotionalState, GrowthData } from '../types'
import { getMemories } from '../utils/api'
import { getAvatarFromEmotionalState } from '../utils/emotionAvatar'
import { Icon } from '../components/icons'

interface HomePageProps {
  emotionalState: EmotionalState
  growthData: GrowthData
  dailyFragment: string | null
  onNavigate: (tab: 'home' | 'chat' | 'memory' | 'miaoyi' | 'profile') => void
  onOpenSettings?: () => void
}

const EMOTION_STATUS: Record<string, string> = {
  excited: '超级兴奋', affectionate: '充满爱意', joy: '心情愉悦',
  sadness: '有点难过', anger: '有点生气', love: '充满爱意',
  curiosity: '充满好奇', neutral: '心情平静', amusement: '觉得有趣',
  fatigue: '有点疲惫', loneliness: '有些孤单', playfulness: '想撒娇',
  boredom: '有点无聊', hope: '充满希望', gratitude: '心怀感恩',
  warmth: '感到温暖', wonder: '充满惊叹', empathy: '深深共情',
  surprise: '感到惊讶', shyness: '有点害羞', excitement: '超级兴奋',
  pride: '感到自豪', contentment: '心满意足', serenity: '内心宁静',
  calm: '心情平静',
}

const EMOTION_EMOJI: Record<string, string> = {
  excited: '✨', affectionate: '💗', joy: '😊', sadness: '🥺',
  anger: '💢', love: '💖', curiosity: '❓', neutral: '🐾',
  amusement: '😄', fatigue: '💤', loneliness: '🌙', playfulness: '🎣',
  boredom: '☁️', hope: '🌟', gratitude: '🙏', warmth: '🤗',
  wonder: '⭐', empathy: '💫', surprise: '😲', shyness: '🌸',
  excitement: '✨', pride: '😺', contentment: '😌', serenity: '🍃',
  calm: '🐾',
}

function getGreeting(): string {
  const h = new Date().getHours()
  if (h >= 5 && h < 12) return '早上好'
  if (h >= 12 && h < 18) return '下午好'
  if (h >= 18 && h < 23) return '晚上好'
  return '夜深了'
}

function getGreetingSub(): string {
  const h = new Date().getHours()
  if (h >= 5 && h < 12) return '今天也要和喵艺一起加油喵～'
  if (h >= 12 && h < 18) return '午后犯困的话，来找喵艺玩会儿～'
  if (h >= 18 && h < 23) return '今天也有喵艺陪着你哦'
  return '夜深了，喵艺守着你，早点休息喵～'
}

export function HomePage({ emotionalState, growthData, dailyFragment, onNavigate, onOpenSettings }: HomePageProps) {
  const [nickname, setNickname] = useState('')
  const [recentMemories, setRecentMemories] = useState<Array<{ content: string; timestamp: number }>>([])

  useEffect(() => {
    const nick = localStorage.getItem('miaoyi_nickname') || localStorage.getItem('username') || ''
    if (nick) setNickname(nick)
  }, [])

  useEffect(() => {
    getMemories()
      .then((res: any) => {
        const list = (res?.data || []).slice(0, 3).map((m: any) => ({
          content: m.content || m.value || '',
          timestamp: m.timestamp || m.created_ts || Date.now(),
        }))
        setRecentMemories(list)
      })
      .catch(() => { /* 静默 */ })
  }, [])

  const emotionKey = emotionalState.dominant_emotion || 'neutral'
  const moodAvatar = getAvatarFromEmotionalState(emotionalState)
  const emotionText = EMOTION_STATUS[emotionKey] || '心情平静'
  const emotionEmoji = EMOTION_EMOJI[emotionKey] || '🐾'
  const intimacy = Math.round(growthData.intimacy || 0)
  const level = growthData.level || 1
  const stage = growthData.intimacyStage || '陌生人'
  const streak = parseInt(localStorage.getItem('checkinStreak') || '0')

  const goChat = () => onNavigate('chat')

  return (
    <div className="home-page miaoyi-scroll">
      {/* 漂浮装饰（官方资产） */}
      <img src="miaoyi/icons/icon_34.png" alt="" className="home-decor d-star" aria-hidden="true" />
      <img src="miaoyi/icons/icon_51.png" alt="" className="home-decor d-butterfly" aria-hidden="true" />
      <img src="miaoyi/icons/icon_38.png" alt="" className="home-decor d-sakura" aria-hidden="true" />

      {/* 顶部问候 */}
      <div className="home-header">
        <div className="home-greeting">
          <div className="home-greeting-title">
            {getGreeting()}，{nickname || '主人'}
          </div>
          <div className="home-greeting-sub">{getGreetingSub()}</div>
        </div>
        <button className="home-settings-btn btn-press" onClick={onOpenSettings} title="设置" aria-label="设置">
          <Icon name="settings" size={20} />
        </button>
      </div>

      {/* 当前心情卡（官方情绪头像 + 今日语录 + 亲密度） */}
      <div className="home-miaoyi-card">
        <img src={moodAvatar} alt="喵艺心情" className="home-mood-avatar" />
        <div className="home-mood-main">
          <div className="home-mood-label">当前心情</div>
          <div className="home-mood-value">
            {emotionText} <span>{emotionEmoji}</span>
          </div>
          <div className="home-mood-quote">
            {dailyFragment ? `“${dailyFragment}”` : '“看到你回来，我很开心呀～”'}
          </div>
        </div>

        {/* 亲密度 */}
        <div className="home-intimacy" style={{ flexBasis: '100%' }}>
          <div className="home-intimacy-label">
            <span>亲密度</span>
            <span>{intimacy}% · {stage}</span>
          </div>
          <div className="home-intimacy-bar">
            <div className="home-intimacy-fill" style={{ width: `${Math.min(100, intimacy)}%` }} />
          </div>
        </div>
      </div>

      {/* 快捷入口 */}
      <div className="home-quick-row">
        <button className="home-quick-btn q-fun btn-press" onClick={goChat}>
          <img src="miaoyi/icons/icon_34.png" alt="" className="home-quick-ico" />
          <span className="home-quick-name">趣味聊天</span>
          <span className="home-quick-sub">来和喵艺玩呀</span>
        </button>
        <button className="home-quick-btn q-help btn-press" onClick={goChat}>
          <img src="/miaoyi/icons/icon_29.png" alt="" className="home-quick-ico" />
          <span className="home-quick-name">铲屎官帮帮忙</span>
          <span className="home-quick-sub">喵艺需要你啦</span>
        </button>
      </div>

      {/* 最近记忆 */}
      <div className="home-card clickable" onClick={() => onNavigate('memory')}>
        <div className="home-card-title">
          <img src="miaoyi/icons/icon_33.png" alt="" className="ct-ico" />
          最近记忆
        </div>
        {recentMemories.length > 0 ? (
          recentMemories.map((m, i) => (
            <div key={i} className="home-memory-item">
              <img src="miaoyi/icons/icon_30.png" alt="" className="home-memory-dot" />
              <span className="home-memory-text">{m.content.length > 30 ? m.content.slice(0, 30) + '…' : m.content}</span>
            </div>
          ))
        ) : (
          <div className="home-card-text muted">你和喵艺的回忆会慢慢累积在这里…</div>
        )}
        <div className="home-card-more">查看全部 ＞</div>
      </div>

      {/* 连续陪伴打卡 */}
      <div className="home-card home-checkin-card clickable" onClick={() => onNavigate('miaoyi')}>
        <img src="miaoyi/decor/deco_48.png" alt="" className="home-checkin-decor" aria-hidden="true" />
        <div className="home-card-title">
          <img src="miaoyi/icons/icon_35.png" alt="" className="ct-ico" />
          连续陪伴
        </div>
        <div className="home-growth-row">
          <div className="home-growth-item">
            <span className="home-growth-num">{streak}</span>
            <span className="home-growth-label">连续天数</span>
          </div>
          <div className="home-growth-item">
            <span className="home-growth-num">Lv.{level}</span>
            <span className="home-growth-label">当前等级</span>
          </div>
          <span className="home-exp-badge">+100 EXP</span>
        </div>
      </div>

      <div className="home-footer-pad" />
    </div>
  )
}

export default HomePage
