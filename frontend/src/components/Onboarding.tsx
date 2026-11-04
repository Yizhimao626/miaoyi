import { useState } from 'react'
import { Icon, type IconName } from './icons'

interface OnboardingOption {
  id: string
  title: string
  description: string
  trigger_text: string
}

interface OnboardingProps {
  greeting: string
  options: OnboardingOption[]
  onSelectOption: (triggerText: string) => void
  onSkip?: () => void
}

export function Onboarding({ greeting, options, onSelectOption, onSkip }: OnboardingProps) {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [isAnimating, setIsAnimating] = useState(false)

  const handleSelectOption = (option: OnboardingOption) => {
    if (isAnimating) return
    
    setSelectedId(option.id)
    setIsAnimating(true)
    
    setTimeout(() => {
      onSelectOption(option.trigger_text)
    }, 300)
  }

  const handleSkip = () => {
    if (isAnimating) return
    onSkip?.()
  }

  return (
    <div className="onboarding-container">
      <div className="onboarding-header">
        <div className="onboarding-cat-icon"><Icon name="cat" size={64} /></div>
        <h1 className="onboarding-title">喵～你好呀！</h1>
        <p className="onboarding-greeting">{greeting}</p>
      </div>
      
      <div className="onboarding-options">
        {options.map((option) => (
          <button
            key={option.id}
            className={`onboarding-option-btn btn-press ${selectedId === option.id ? 'selected' : ''}`}
            onClick={() => handleSelectOption(option)}
            disabled={isAnimating}
          >
            <div className="option-icon"><Icon name={getOptionIcon(option.id)} size={28} /></div>
            <div className="option-content">
              <div className="option-title">{option.title}</div>
              <div className="option-description">{option.description}</div>
            </div>
            <div className="option-arrow">→</div>
          </button>
        ))}
      </div>
      
      <div className="onboarding-footer">
        <p className="onboarding-hint">点击任意一个开始我们的故事～</p>
        <button 
          className="onboarding-skip-btn" 
          onClick={handleSkip}
          disabled={isAnimating}
          type="button"
          style={{ 
            display: 'block',
            visibility: 'visible',
            opacity: 1
          }}
        >
          跳过，直接聊天 →
        </button>
      </div>
    </div>
  )
}

function getOptionIcon(id: string): IconName {
  const icons: Record<string, IconName> = {
    nickname: 'paw',
    mood: 'crown',
    secret: 'gift',
  }
  return icons[id] || 'sparkles'
}
