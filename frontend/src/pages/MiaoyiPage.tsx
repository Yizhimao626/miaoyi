import { useState, useEffect } from 'react'
import type { EmotionalState, GrowthData } from '../types'
import { getRelationshipStatus } from '../utils/api'
import { Icon, type IconName } from '../components/icons'

interface MiaoyiPageProps {
  emotionalState: EmotionalState
  growthData: GrowthData
}

const EMOTION_EMOJI: Record<string, string> = {
  excited: '🤩', affectionate: '🥰', joy: '😊', sadness: '😢',
  anger: '😤', love: '🥰', curiosity: '🤔', neutral: '😊',
  amusement: '😂', fatigue: '😴', loneliness: '🥺', playfulness: '😜',
  boredom: '😑', hope: '🌟', gratitude: '🙏', warmth: '🤗',
  wonder: '✨', empathy: '💫', surprise: '😲', shyness: '😳',
  excitement: '🤩', pride: '😎', contentment: '😌', serenity: '🧘',
  calm: '😌',
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

const ABILITIES: { id: string; name: string; icon: IconName; unlocked: boolean; desc: string }[] = [
  { id: 'emotion', name: '情感理解', icon: 'heart', unlocked: true, desc: '读懂你的心情' },
  { id: 'memory', name: '长期记忆', icon: 'brain', unlocked: true, desc: '记住重要的事' },
  { id: 'proactive', name: '主动聊天', icon: 'mail', unlocked: true, desc: '会主动想起你' },
  { id: 'voice', name: '语音交流', icon: 'volume', unlocked: true, desc: '用声音陪伴你' },
  { id: 'deepthink', name: '深度思考', icon: 'moon', unlocked: true, desc: '认真想问题' },
  { id: 'future', name: '更多个性化', icon: 'lock', unlocked: false, desc: '敬请期待' },
]

// 【方案一·沉浸式角色舞台】
// 一次只展示一个形态的去底立绘，通过"切换形态"按钮淡入切换，
// 不再把素材堆砌在同一个框里。
type CharacterForm = 'cute' | 'magic'

const FORM_META: Record<CharacterForm, { img: string; name: string; label: string; switchTo: string }> = {
  cute: {
    img: 'miaoyi/characters/cute_portrait.jpg',
    name: '可爱风',
    label: '可爱风 · 治愈陪伴',
    switchTo: '切换 · 魔法风',
  },
  magic: {
    img: 'miaoyi/characters/magic_portrait.jpg',
    name: '魔法风',
    label: '魔法风 · 高级人格',
    switchTo: '切换 · 可爱风',
  },
}

export function MiaoyiPage({ emotionalState, growthData }: MiaoyiPageProps) {
  const [relationshipDays, setRelationshipDays] = useState<number>(1)
  // 形态选择持久化：记住用户上次切换的形态
  const [form, setForm] = useState<CharacterForm>(() => {
    const saved = localStorage.getItem('miaoyi_character_form')
    return saved === 'magic' ? 'magic' : 'cute'
  })

  useEffect(() => {
    localStorage.setItem('miaoyi_character_form', form)
  }, [form])

  useEffect(() => {
    getRelationshipStatus()
      .then((res: any) => {
        const days = res?.data?.days || res?.data?.relation_days
        if (days) setRelationshipDays(days)
      })
      .catch(() => {})
  }, [])

  const emotionKey = emotionalState.dominant_emotion || 'neutral'
  const emotionEmoji = EMOTION_EMOJI[emotionKey] || '😊'
  const emotionText = EMOTION_STATUS[emotionKey] || '心情平静'
  const intimacy = Math.round(growthData.intimacy || 0)
  const level = growthData.level || 1
  const stage = growthData.intimacyStage || '陌生人'
  const meta = FORM_META[form]

  const personalityTags = ['温柔', '黏人', '好奇', '活泼']

  return (
    <div className="miaoyi-page miaoyi-scroll">
      {/* 沉浸式角色舞台：单形态照片卡，破框而出。
          旧版把信息/切换控件都堆在图上，立绘头部被标题遮挡、文字被按钮裁切；
          现改为：顶部浮层只放名字心情（带渐隐遮罩），切换控件移到卡片外 */}
      <div className="miaoyi-stage">
        <div className="miaoyi-stage-glow" aria-hidden="true" />
        <span className="miaoyi-stage-sparkle s1" aria-hidden="true">✦</span>
        <span className="miaoyi-stage-sparkle s2" aria-hidden="true">✧</span>
        <span className="miaoyi-stage-sparkle s3" aria-hidden="true">✦</span>

        {/* key 绑定形态：切换时重新挂载，触发淡入动画 */}
        <img
          key={form}
          src={meta.img}
          alt={`喵艺 · ${meta.name}`}
          className="miaoyi-stage-img"
        />

        <div className="miaoyi-stage-scrim" aria-hidden="true" />
        <div className="miaoyi-stage-info">
          <div className="miaoyi-hero-name">
            喵艺<span className="miaoyi-hero-level">Lv.{level}</span>
          </div>
          <div className="miaoyi-hero-mood">
            {emotionEmoji} {emotionText} · {stage}
          </div>
        </div>
      </div>

      {/* 形态切换：卡片外独立一行，彻底避免与立绘/文字重叠 */}
      <div className="miaoyi-form-row">
        <span className="miaoyi-form-label">{meta.label}</span>
        <button
          className="miaoyi-form-toggle btn-press"
          onClick={() => setForm(form === 'cute' ? 'magic' : 'cute')}
        >
          {meta.switchTo}
        </button>
      </div>

      {/* 亲密度 */}
      <div className="miaoyi-card">
        <div className="miaoyi-card-title">
          <img src="miaoyi/icons/icon_31.png" alt="" className="ct-ico" />
          亲密度
        </div>
        <div className="miaoyi-intimacy-value">{intimacy} / 100</div>
        <div className="home-intimacy-bar">
          <div className="home-intimacy-fill" style={{ width: `${Math.min(100, intimacy)}%` }} />
        </div>
        <div className="miaoyi-relation-row">
          <span className="miaoyi-relation-badge">{stage}</span>
          <span className="miaoyi-relation-days">相识 {Math.max(1, relationshipDays)} 天</span>
        </div>
      </div>

      {/* 性格 */}
      <div className="miaoyi-card">
        <div className="miaoyi-card-title">
          <img src="miaoyi/icons/icon_49.png" alt="" className="ct-ico" />
          性格特征
        </div>
        <div className="miaoyi-personality-tags">
          {personalityTags.map((t) => (
            <span key={t} className="miaoyi-tag">
              <img src="miaoyi/icons/icon_30.png" alt="" className="tag-paw" />
              {t}
            </span>
          ))}
        </div>
      </div>

      {/* 成长 */}
      <div className="miaoyi-card">
        <div className="miaoyi-card-title">
          <img src="miaoyi/icons/icon_34.png" alt="" className="ct-ico" />
          成长
        </div>
        <div className="miaoyi-growth-bar-label">
          <span>EXP</span>
          <span>{growthData.exp || 0} / {growthData.expMax || 100}</span>
        </div>
        <div className="home-intimacy-bar">
          <div className="home-intimacy-fill" style={{ width: `${Math.min(100, ((growthData.exp || 0) / (growthData.expMax || 100)) * 100)}%` }} />
        </div>
      </div>

      {/* 已解锁能力 */}
      <div className="miaoyi-card">
        <div className="miaoyi-card-title">
          <img src="miaoyi/icons/icon_40.png" alt="" className="ct-ico" />
          已解锁能力
        </div>
        <div className="miaoyi-abilities">
          {ABILITIES.map((a) => (
            <div key={a.id} className={`miaoyi-ability ${a.unlocked ? '' : 'locked'}`}>
              <span className="miaoyi-ability-icon">
                <Icon name={a.unlocked ? a.icon : 'lock'} size={22} />
              </span>
              <div className="miaoyi-ability-info">
                <div className="miaoyi-ability-name">
                  {a.name}
                  {a.unlocked && <span className="miaoyi-ability-check"><Icon name="check" size={10} /></span>}
                </div>
                <div className="miaoyi-ability-desc">{a.desc}</div>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="home-footer-pad" />
    </div>
  )
}

export default MiaoyiPage
