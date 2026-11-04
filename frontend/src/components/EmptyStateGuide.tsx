// -*- coding: utf-8 -*-
/**
 * 喵艺空状态引导组件
 * 
 * 当聊天列表为空时显示可爱的猫咪打哈欠动画和快捷问题按钮
 * 
 * 功能：
 * - CSS 纯动画猫咪（耳朵、眼睛、鼻子、嘴巴、尾巴）
 * - 呼吸、眨眼、打哈欠、摇尾巴、ZZZ 漂浮动画
 * - 快捷问题按钮，点击即可发送
 */
import React from 'react'
import { Icon, type IconName } from './icons'

interface EmptyStateGuideProps {
  onSendMessage: (text: string) => void
  onHaptic?: (style?: 'light' | 'medium' | 'heavy' | 'success' | 'warning' | 'error') => void
}

// 预设快捷问题【第6步优化】：更贴近情感陪伴场景的猫系话题
const QUICK_QUESTIONS: { text: string; icon: IconName }[] = [
  { text: '我今天心情不太好…', icon: 'cloudRain' },
  { text: '听我碎碎念一会儿', icon: 'messageSquare' },
  { text: '给我讲个可爱的笑话', icon: 'smile' },
  { text: '你现在的猫生在干嘛？', icon: 'paw' },
  { text: '给我写首小诗吧', icon: 'sparkles' },
]

export const EmptyStateGuide: React.FC<EmptyStateGuideProps> = ({
  onSendMessage,
  onHaptic
}) => {
  const handleQuickQuestion = (text: string) => {
    onHaptic?.('light')
    onSendMessage(text)
  }

  return (
    <div className="empty-state-guide">
      {/* 【第三步】睡眠/待机插画（替代 CSS 猫咪动画） */}
      <div className="empty-cat-animation">
        <img src="miaoyi/illustrations/sleep.png" alt="喵艺在休息" className="empty-sleep-img" />
        <div className="zzz">z<sup>z</sup>Z</div>
      </div>

      {/* 提示文字 */}
      <div className="empty-state-text">
        <div className="empty-title">喵～ 说点什么吧，本喵快睡着了...</div>
        <div className="empty-subtitle">可以试试下面的话题喵~</div>
      </div>

      {/* 快捷问题按钮 */}
      <div className="empty-quick-questions">
        {QUICK_QUESTIONS.map((q, idx) => (
          <button
            key={idx}
            className="quick-question-btn btn-press"
            onClick={() => handleQuickQuestion(q.text)}
          >
            <span className="question-icon"><Icon name={q.icon} size={20} /></span>
            <span className="question-text">{q.text}</span>
          </button>
        ))}
      </div>
    </div>
  )
}

export default EmptyStateGuide
