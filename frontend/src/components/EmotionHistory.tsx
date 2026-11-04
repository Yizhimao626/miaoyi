import { useEffect, useState } from 'react'
import { getEmotionalHistory } from '../utils/api'

interface HistoryRecord {
  timestamp: number
  vector: Record<string, number>
}

const EMOTION_META: Record<string, { emoji: string; name: string; color: string }> = {
  joy: { emoji: '😊', name: '开心', color: '#FFC107' },
  love: { emoji: '🥰', name: '喜爱', color: '#FF69B4' },
  sadness: { emoji: '😢', name: '难过', color: '#6495ED' },
  anger: { emoji: '😠', name: '生气', color: '#FF6347' },
  fear: { emoji: '😨', name: '害怕', color: '#9B59B6' },
  excitement: { emoji: '🤩', name: '兴奋', color: '#FF9A76' },
  contentment: { emoji: '😌', name: '满足', color: '#2ED573' },
  gratitude: { emoji: '🙏', name: '感激', color: '#4CD964' },
  curiosity: { emoji: '🤔', name: '好奇', color: '#00BCD4' },
}

const TRACK_DIMS = ['joy', 'sadness', 'love', 'excitement']

function dominantOf(vector: Record<string, number>): { key: string; value: number } {
  let maxKey = 'contentment'
  let maxVal = 0
  for (const [k, v] of Object.entries(vector)) {
    if (v > maxVal) { maxVal = v; maxKey = k }
  }
  return { key: maxKey, value: maxVal }
}

function formatTime(ts: number): string {
  const d = new Date(ts * 1000)
  const hh = d.getHours().toString().padStart(2, '0')
  const mm = d.getMinutes().toString().padStart(2, '0')
  const month = d.getMonth() + 1
  const day = d.getDate()
  return `${month}/${day} ${hh}:${mm}`
}

export function EmotionHistory() {
  const [history, setHistory] = useState<HistoryRecord[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    load()
  }, [])

  const load = async () => {
    try {
      const result = await getEmotionalHistory()
      const raw = result.data || []
      // 按时间排序（升序）并取最近 60 条
      const sorted = [...raw].sort((a, b) => a.timestamp - b.timestamp).slice(-60)
      setHistory(sorted)
    } catch {
      // 静默失败，展示空态
    } finally {
      setLoading(false)
    }
  }

  // ---------- SVG 折线图 ----------
  const W = 320
  const H = 140
  const PAD = 8

  const renderChart = () => {
    if (history.length < 2) return null
    const points = history
    const maxVal = 1.0
    const stepX = (W - PAD * 2) / Math.max(1, points.length - 1)
    const yOf = (v: number) => H - PAD - (Math.max(0, Math.min(1, v)) / maxVal) * (H - PAD * 2)

    return (
      <svg viewBox={`0 0 ${W} ${H}`} className="emotion-chart-svg" preserveAspectRatio="none">
        {/* 网格线 */}
        {[0.25, 0.5, 0.75, 1].map((t) => (
          <line key={t} x1={PAD} x2={W - PAD} y1={yOf(t)} y2={yOf(t)} className="emotion-chart-grid" />
        ))}
        {/* 各维度折线 */}
        {TRACK_DIMS.map((dim) => {
          const meta = EMOTION_META[dim]
          if (!meta) return null
          const line = points.map((p, i) => {
            const x = PAD + i * stepX
            const y = yOf(p.vector?.[dim] || 0)
            return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
          }).join(' ')
          return <path key={dim} d={line} fill="none" stroke={meta.color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="emotion-chart-line" />
        })}
        {/* 图例 */}
        {TRACK_DIMS.map((dim) => {
          const meta = EMOTION_META[dim]
          if (!meta) return null
          return (
            <g key={`legend-${dim}`} transform={`translate(${PAD + TRACK_DIMS.indexOf(dim) * 62}, 8)`}>
              <circle r="4" fill={meta.color} />
              <text x="7" y="4" className="emotion-chart-legend">{meta.name}</text>
            </g>
          )
        })}
      </svg>
    )
  }

  if (loading) {
    return <div className="emotion-history-empty">🐾 正在翻看心情日记…</div>
  }

  if (history.length === 0) {
    return (
      <div className="emotion-history-empty">
        <div className="empty-emoji">💗</div>
        <p>还没有心情记录</p>
        <span>多和喵艺聊聊，她的小心情就会记录下来哦～</span>
      </div>
    )
  }

  return (
    <div className="emotion-history">
      <div className="emotion-chart-card">
        <h4 className="emotion-chart-title">💗 心情变化曲线</h4>
        {renderChart()}
      </div>
      <div className="emotion-history-list">
        {[...history].reverse().map((rec, idx) => {
          const dom = dominantOf(rec.vector || {})
          const meta = EMOTION_META[dom.key] || { emoji: '🐾', name: dom.key, color: '#999' }
          const pct = Math.round(dom.value * 100)
          return (
            <div key={`${rec.timestamp}-${idx}`} className="emotion-history-item">
              <div className="emotion-history-emoji" style={{ background: `${meta.color}22` }}>{meta.emoji}</div>
              <div className="emotion-history-info">
                <div className="emotion-history-name">
                  {meta.name}
                  <span className="emotion-history-pct">{pct}%</span>
                </div>
                <div className="emotion-history-bar-bg">
                  <div className="emotion-history-bar-fill" style={{ width: `${pct}%`, background: meta.color }} />
                </div>
              </div>
              <div className="emotion-history-time">{formatTime(rec.timestamp)}</div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
