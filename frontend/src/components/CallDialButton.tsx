import { useCallStore } from '../store/callStore'
import { Icon } from './icons'

/**
 * 拨号按钮组件 —— 带状态联动
 * 空闲时显示 📞 绿色，点击发起通话
 * 通话中显示 📵 红色脉冲，点击挂断
 * 【ChatView 拆分·第1步】从 ChatView.tsx 独立出来
 */
export function CallDialButton() {
  const callStatus = useCallStore((s) => s.status)
  const isInCall = callStatus !== 'idle'

  return (
    <button
      className={`call-btn btn-press ${isInCall ? 'calling' : ''}`}
      onClick={() => {
        if (isInCall) {
          useCallStore.getState().endCall()
        } else {
          useCallStore.getState().startOutgoing()
        }
      }}
      title={isInCall ? '挂断' : '给喵艺打电话'}
      aria-label={isInCall ? '挂断电话' : '拨打语音电话'}
    >
      {isInCall ? <Icon name="phoneOff" size={20} /> : <Icon name="phone" size={20} />}
    </button>
  )
}
