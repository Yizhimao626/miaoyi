import { useState } from 'react'
import { Icon } from '../icons'

interface AboutTabProps {
  onSubmitFeedback: (content: string) => void
  onShowToast: (message: string, type?: 'success' | 'error' | 'warning') => void
}

export function AboutTab({ onSubmitFeedback, onShowToast }: AboutTabProps) {
  const [feedbackContent, setFeedbackContent] = useState('')
  const [showFeedbackForm, setShowFeedbackForm] = useState(false)

  const handleSubmit = async () => {
    if (!feedbackContent.trim()) {
      onShowToast('请输入反馈内容', 'warning')
      return
    }
    try {
      await onSubmitFeedback(feedbackContent)
      onShowToast('反馈提交成功！', 'success')
      setShowFeedbackForm(false)
      setFeedbackContent('')
    } catch {
      onShowToast('提交失败，请重试', 'error')
    }
  }

  return (
    <div className="about-panel">
      <div className="about-logo"><Icon name="cat" size={44} /></div>
      <div className="about-title">喵艺</div>
      <div className="about-version">版本 v13.0</div>
      <div className="settings-divider" />
      <div className="about-info">
        <p><Icon name="heart" size={14} /> 一款有灵魂的AI聊天伙伴</p>
        <p><Icon name="sparkles" size={14} /> 支持多维度情感系统、记忆功能、语音交互</p>
      </div>
      <div className="settings-divider" />
      <div className="about-developer">
        <p><Icon name="user" size={14} /> 开发者：喵艺团队</p>
        <p><Icon name="mail" size={14} /> 联系邮箱：support@miaoyi.com</p>
      </div>
      <div className="settings-divider" />
      {showFeedbackForm ? (
        <div className="feedback-form">
          <textarea
            className="feedback-textarea"
            placeholder="请输入您的反馈意见..."
            value={feedbackContent}
            onChange={(e) => setFeedbackContent(e.target.value)}
            rows={4}
          />
          <div className="feedback-actions">
            <button className="settings-btn btn-press" onClick={() => {
              setShowFeedbackForm(false)
              setFeedbackContent('')
            }}>取消</button>
            <button className="settings-btn checkin btn-press" onClick={handleSubmit}>提交</button>
          </div>
        </div>
      ) : (
        <button className="settings-action-btn checkin btn-press" onClick={() => setShowFeedbackForm(true)}>
          <Icon name="messageSquare" size={16} /> 用户反馈
        </button>
      )}
    </div>
  )
}