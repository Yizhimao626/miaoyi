import { useEffect, useState } from 'react'
import { Icon, type IconName } from './icons'

interface ToastProps {
  message: string
  type: 'success' | 'error' | 'warning' | 'info' | 'cat'
  onComplete: () => void
  duration?: number
}

// 猫语翻译器：将普通操作反馈转换为喵艺风格
const CAT_TRANSLATIONS: Record<string, string> = {
  '已删除': '已删除喵～',
  '已清空': '已清空喵～',
  '已保存': '设置保存成功啦喵！',
  '已复制': '已复制到剪贴板喵～',
  '已发送': '发送成功啦喵～',
  '删除成功': '删除成功喵～',
  '保存成功': '保存成功啦喵！',
  '操作成功': '操作成功喵～',
}

function getCatMessage(message: string, type: string): string {
  // 如果已经是猫语风格，直接返回
  if (message.includes('喵')) return message
  
  // 尝试匹配已知的操作反馈
  for (const [key, value] of Object.entries(CAT_TRANSLATIONS)) {
    if (message.includes(key)) {
      return value
    }
  }
  
  // 根据类型添加猫语后缀
  if (type === 'success') {
    return `${message}喵～`
  } else if (type === 'error') {
    return `${message}...呜`
  }
  return message
}

function getToastIcon(type: string): IconName {
  switch (type) {
    case 'success': return 'check'
    case 'error': return 'x'
    case 'warning': return 'alert'
    case 'cat': return 'cat'
    default: return 'info'
  }
}

function getToastColor(type: string): string {
  switch (type) {
    case 'success': return '#22c55e'
    case 'error': return '#ef4444'
    case 'warning': return '#f59e0b'
    case 'cat': return '#FF9A76'
    default: return '#3b82f6'
  }
}

export function Toast({ message, type, onComplete, duration = 2500 }: ToastProps) {
  const [isVisible, setIsVisible] = useState(false)
  const catMessage = getCatMessage(message, type)
  const icon = getToastIcon(type)
  const color = getToastColor(type)

  useEffect(() => {
    // 触发进入动画
    requestAnimationFrame(() => setIsVisible(true))
    
    const timer = setTimeout(() => {
      setIsVisible(false)
      setTimeout(onComplete, 300) // 等待退出动画完成
    }, duration)

    return () => {
      clearTimeout(timer)
      setIsVisible(false)
    }
  }, [onComplete, duration])

  return (
    <div 
      className={`toast-container ${isVisible ? 'show' : ''}`}
      style={{ '--toast-color': color } as React.CSSProperties}
      role="alert"
      aria-live="polite"
    >
      {/* 毛玻璃背景 */}
      <div className="toast-glass" />
      
      {/* 内容区域 */}
      <div className={`toast-content ${type}`}>
        {/* 图标 */}
        <div className={`toast-icon ${type}`}>
          {type === 'success' ? (
            <svg viewBox="0 0 24 24" className="checkmark">
              <circle className="checkmark-circle" cx="12" cy="12" r="11" />
              <path className="checkmark-check" d="M8 12l3 3 5-5" />
            </svg>
          ) : (
            <span className="icon-text"><Icon name={icon} size={16} /></span>
          )}
        </div>
        
        {/* 消息文本 */}
        <div className="toast-message">{catMessage}</div>
        
        {/* 猫爪装饰 */}
        {type === 'cat' && <span className="toast-paw"><Icon name="paw" size={16} /></span>}
      </div>
      
      {/* 进度条 */}
      <div className="toast-progress-bar">
        <div className="progress-fill" />
      </div>
    </div>
  )
}
