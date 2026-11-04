import './screen-time.css'
import { Icon } from './icons'

interface ScreenTimeReminderProps {
  /** 已连续使用时长（分钟） */
  minutes: number
  onConfirm: () => void
}

/** 【合规】连续使用 2 小时强制休息提醒（2026-07-15 新规要求）
 *  全屏遮罩弹窗，必须点击确认才能关闭，重置计时后继续使用。 */
export function ScreenTimeReminder({ minutes, onConfirm }: ScreenTimeReminderProps) {
  const hours = Math.floor(minutes / 60)
  const mins = minutes % 60

  return (
    <div className="screen-time-overlay" role="dialog" aria-modal="true" aria-label="休息提醒">
      <div className="screen-time-card">
        <div className="screen-time-icon"><Icon name="moon" size={40} /></div>
        <h3 className="screen-time-title">你已经陪喵艺 {hours > 0 ? `${hours} 小时` : ''}{mins > 0 ? ` ${mins} 分钟` : ''}啦</h3>
        <p className="screen-time-text">
          该起来活动一下眼睛和身体咯～
          <br />
          喝口水、看看窗外，喵艺会一直在这里等你回来
        </p>
        <button className="screen-time-btn btn-press" onClick={onConfirm}>
          好的，我去休息一下
        </button>
        <p className="screen-time-footnote">长时间连续使用可能影响休息与健康，请注意合理安排时间</p>
      </div>
    </div>
  )
}
