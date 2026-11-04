/**
 * 思考状态指示器 —— 从 ChatView 拆分（2026-08，重构第 3 步）
 * 纯展示组件：喵艺思考/检索/验证/打字的多阶段状态 + 进度条 + 四步指示（理解/回忆/分析/组织）。
 * 数据与文案（THINKING_MESSAGES）随之迁出，ChatView 仅透传 thinking* props。
 */
import { Icon } from './icons'

// 思考状态文案 - 猫语多阶段反馈（【规范】步骤化：理解/回忆/分析/组织）
const THINKING_MESSAGES = {
  thinking: [
    '喵艺正在认真想……',
    '理解你的问题中…',
    '竖起耳朵，仔细听你说…',
  ],
  searching: [
    '回忆相关内容中…',
    '翻翻我们之前的对话…',
    '去记忆里找一找…',
  ],
  verifying: [
    '分析你的意思中…',
    '确认一下理解对不对…',
    '认真思考你的话…',
  ],
  typing: [
    '组织回答中…',
    '马上就好，正在打字',
    '思考完毕，开写！',
  ],
}

// 获取随机思考文案
function getThinkingMessage(status: string, stepIndex: number): string {
  const messages = THINKING_MESSAGES[status as keyof typeof THINKING_MESSAGES] || THINKING_MESSAGES.thinking
  const index = stepIndex % messages.length
  return messages[index]
}

interface ThinkingIndicatorProps {
  status: string
  step?: string
  progress?: number
  stepIndex?: number
  totalSteps?: number
}

export function ThinkingIndicator({
  status,
  step = '',
  progress = 0,
  stepIndex = 0,
  totalSteps = 4,
}: ThinkingIndicatorProps) {
  if (status === 'idle') return null

  return (
        <div className={`thinking-indicator-bar thinking-${status}`}>
          <span className="thinking-icon">
            <Icon
              name={status === 'searching' ? 'search'
                : status === 'verifying' ? 'check'
                : status === 'typing' ? 'edit' : 'brain'}
              size={16}
            />
          </span>
          <div className="thinking-content">
            <span className="thinking-text">
              {step || getThinkingMessage(status, stepIndex)}
            </span>
            {/* 进度条 */}
            <div className="thinking-progress-bar">
              <div 
                className="thinking-progress-fill" 
                style={{ width: `${progress}%` }}
              />
            </div>
            {/* 步骤计数 */}
            <span className="thinking-step-count">
              {Math.min(stepIndex + 1, totalSteps)}/{totalSteps}
            </span>
            {/* 步骤指示器 - 规范四步：理解/回忆/分析/组织 */}
            <div className="thinking-steps">
              {['理解', '回忆', '分析', '组织'].map((label, idx) => (
                <span 
                  key={label} 
                  className={`step-dot ${idx <= stepIndex ? 'active' : ''} ${idx === stepIndex ? 'current' : ''}`}
                >
                  {label}
                </span>
              ))}
            </div>
          </div>
          <span className="thinking-dots">
            <span className="dot" />
            <span className="dot" />
            <span className="dot" />
          </span>
        </div>
  )
}
