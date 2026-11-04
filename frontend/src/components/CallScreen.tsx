/**
 * 喵艺 · 通话全屏界面
 * 一个组件覆盖：拨号中 / 来电响铃 / 通话中 / 已结束
 * 用法：<CallScreen /> 挂在 App 根节点，内部根据 status 决定是否渲染
 *
 * 【ADAPT】已应用路径修正：项目实际是 src/store（无 s）。
 */
import { useEffect, useState } from 'react'
import { useCallStore, answerIncoming } from '../store/callStore'
import { useVoiceDialog } from '../hooks/useVoiceDialog'
import { RealtimeSubtitle } from './RealtimeSubtitle'
import { Icon } from './icons'
import '../styles/im-skin.css'

export function CallScreen() {
  const { status, direction, duration, incomingReason, micOn, speakerOn, endedReason,
    endCall, toggleMic, toggleSpeaker } = useCallStore()
  // startOutgoing 由外部拨号按钮通过 useCallStore.getState().startOutgoing() 调用，此处不解构

  useVoiceDialog()

  // 【修复】麦克风错误提示：useVoiceDialog 在权限被拒等失败路径写入 window.__callMicError，
  // 成功获取麦克风流时清空。此处轮询展示，避免"通话接通了但对方永远听不见"的无提示故障。
  const [micError, setMicError] = useState('')
  useEffect(() => {
    if (status !== 'connected') { setMicError(''); return }
    setMicError((window as unknown as { __callMicError?: string }).__callMicError || '')
    const t = setInterval(() => {
      setMicError((window as unknown as { __callMicError?: string }).__callMicError || '')
    }, 500)
    return () => clearInterval(t)
  }, [status])

  useEffect(() => {
    if (status !== 'connected') return
    const t = setInterval(() => useCallStore.getState().tickDuration(), 1000)

    // 通话中屏幕常亮（Wake Lock API，降级忽略不支持）
    let wakeLock: any = null
    if ('wakeLock' in navigator) {
      try {
        ;(navigator as any).wakeLock.request('screen').then((wl: any) => { wakeLock = wl }).catch(() => {})
      } catch {}
    }

    return () => {
      clearInterval(t)
      try { wakeLock?.release?.() } catch {}
    }
  }, [status])

  if (status === 'idle') return null

  const fmt = (s: number) => `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`

  return (
    <div className="ms-call-overlay">
      <div className="ms-call-blur" />

      {status === 'ringing' && direction === 'incoming' && (
        <div className="ms-call-card ms-call-incoming">
          <div className="ms-call-name">喵艺</div>
          <div className="ms-call-reason">{incomingReason}</div>
          <div className="ms-call-avatar pulse"><Icon name="cat" size={46} /></div>
          <div className="ms-call-status">来电中…</div>
          <div className="ms-call-actions">
            <button className="ms-call-btn decline" onClick={() => endCall('已拒绝')}>
              <span>拒接</span>
            </button>
            <button className="ms-call-btn accept" onClick={answerIncoming}>
              <span>接听</span>
            </button>
          </div>
        </div>
      )}

      {status === 'dialing' && (
        <div className="ms-call-card">
          <div className="ms-call-avatar"><Icon name="cat" size={46} /></div>
          <div className="ms-call-name">喵艺</div>
          <div className="ms-call-status">正在拨打…</div>
          <div className="ms-call-actions">
            <button className="ms-call-btn decline" onClick={() => endCall('已取消')}>取消</button>
          </div>
        </div>
      )}

      {status === 'ringing' && direction === 'outgoing' && (
        <div className="ms-call-card">
          <div className="ms-call-avatar ring"><Icon name="cat" size={46} /></div>
          <div className="ms-call-name">喵艺</div>
          <div className="ms-call-status">嘟嘟嘟…等待接听</div>
          <div className="ms-call-actions">
            <button className="ms-call-btn decline" onClick={() => endCall('已取消')}>取消</button>
          </div>
        </div>
      )}

      {status === 'connected' && (
        <div className="ms-call-card ms-call-connected">
          <div className="ms-call-avatar small"><Icon name="cat" size={36} /></div>
          <div className="ms-call-name">喵艺</div>
          <div className="ms-call-duration">{fmt(duration)}</div>
          {micError && <div className="ms-call-mic-error">{micError}</div>}
          <RealtimeSubtitle />
          <div className="ms-call-actions connected">
            <button className={`ms-call-btn ctrl ${micOn ? 'on' : 'off'}`} onClick={toggleMic}>
              <span className="call-ctrl-inline">
                {micOn ? <Icon name="mic" size={18} /> : <Icon name="micOff" size={18} />}
                {micOn ? '开' : '关'}
              </span>
            </button>
            <button className="ms-call-btn decline big" onClick={() => endCall()}>挂断</button>
            <button className={`ms-call-btn ctrl ${speakerOn ? 'on' : 'off'}`} onClick={toggleSpeaker}>
              {speakerOn ? <Icon name="volume" size={20} /> : <Icon name="volumeOff" size={20} />}
            </button>
          </div>
        </div>
      )}

      {status === 'ended' && (
        <div className="ms-call-card">
          <div className="ms-call-avatar"><Icon name="cat" size={46} /></div>
          <div className="ms-call-ended">{endedReason || '通话结束'}</div>
        </div>
      )}
    </div>
  )
}
