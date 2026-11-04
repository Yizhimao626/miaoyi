/**
 * 喵艺 · 通话实时字幕
 *
 * 【ADAPT】已应用路径修正：项目实际是 src/store（无 s）。
 */
import { useEffect, useRef } from 'react'
import { useCallStore } from '../store/callStore'

export function RealtimeSubtitle() {
  const subtitles = useCallStore((s) => s.subtitles)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    ref.current?.scrollTo({ top: ref.current.scrollHeight, behavior: 'smooth' })
  }, [subtitles])

  return (
    <div className="ms-subtitle-box" ref={ref}>
      {subtitles.length === 0 && <div className="ms-subtitle-hint">说话或安静地听她讲…</div>}
      {subtitles.map((s, i) => (
        <div key={i} className={`ms-subtitle-line ${s.speaker}`}>
          {s.speaker === 'bot' ? '喵艺：' : '我：'}{s.text}
        </div>
      ))}
    </div>
  )
}
