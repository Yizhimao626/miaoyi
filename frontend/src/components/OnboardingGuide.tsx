import { useState, useEffect, useCallback } from 'react'
import { Icon } from './icons'

interface GuideStep {
  id: string
  title: string
  description: string
  // 提示气泡的位置
  position: 'top' | 'bottom' | 'left' | 'right'
  // 需要高亮的目标元素选择器
  targetSelector?: string
  // 高亮区域的额外边距
  padding?: number
}

interface OnboardingGuideProps {
  isOpen: boolean
  onComplete: () => void
}

const GUIDE_STEPS: GuideStep[] = [
  {
    id: 'input',
    title: '和喵艺说说话吧～',
    description: '在这里输入你的消息，喵艺会认真倾听哦',
    position: 'top',
    targetSelector: '.input-area',
    padding: 8,
  },
  {
    id: 'messages',
    title: '长按消息有惊喜',
    description: '长按任意消息可以复制、回复或删除哦',
    position: 'bottom',
    targetSelector: '.message-item',
    padding: 12,
  },
  {
    id: 'emotion',
    title: '喵艺也有小情绪',
    description: '看左上角的喵头图标，它会根据对话变换表情呢',
    position: 'bottom',
    targetSelector: '.emotion-avatar',
    padding: 10,
  },
  {
    id: 'nav',
    title: '发现更多精彩',
    description: '点击底部导航，可以发现猫生碎片和个人中心',
    position: 'top',
    targetSelector: '.bottom-nav',
    padding: 8,
  },
]

const STORAGE_KEY = 'miaoyi_onboarding_shown'

export function OnboardingGuide({ isOpen, onComplete }: OnboardingGuideProps) {
  const [currentStep, setCurrentStep] = useState(0)
  const [highlightRect, setHighlightRect] = useState<DOMRect | null>(null)
  const [isAnimating, setIsAnimating] = useState(false)

  // 检查是否需要显示
  useEffect(() => {
    if (!isOpen) return
    
    const hasShown = localStorage.getItem(STORAGE_KEY)
    if (hasShown) {
      onComplete()
    }
  }, [isOpen, onComplete])

  // 更新高亮位置
  useEffect(() => {
    if (!isOpen) return
    
    const step = GUIDE_STEPS[currentStep]
    if (!step?.targetSelector) {
      setHighlightRect(null)
      return
    }

    const updatePosition = () => {
      const el = document.querySelector(step.targetSelector as string)
      if (el) {
        const rect = el.getBoundingClientRect()
        setHighlightRect(rect)
        setIsAnimating(true)
        setTimeout(() => setIsAnimating(false), 300)
      }
    }

    // 等待一下让 DOM 渲染完成
    requestAnimationFrame(updatePosition)
    window.addEventListener('resize', updatePosition)
    window.addEventListener('scroll', updatePosition, true)

    return () => {
      window.removeEventListener('resize', updatePosition)
      window.removeEventListener('scroll', updatePosition, true)
    }
  }, [isOpen, currentStep])

  const handleNext = useCallback(() => {
    if (currentStep < GUIDE_STEPS.length - 1) {
      setCurrentStep(prev => prev + 1)
    } else {
      // 完成所有步骤
      localStorage.setItem(STORAGE_KEY, 'true')
      onComplete()
    }
  }, [currentStep, onComplete])

  const handleSkip = useCallback(() => {
    localStorage.setItem(STORAGE_KEY, 'true')
    onComplete()
  }, [onComplete])

  if (!isOpen) return null

  const step = GUIDE_STEPS[currentStep]
  const progress = ((currentStep + 1) / GUIDE_STEPS.length) * 100

  // 计算提示气泡位置
  const getBubbleStyle = (): React.CSSProperties => {
    if (!highlightRect) {
      return {
        bottom: '80px',
        left: '50%',
        transform: 'translateX(-50%)',
      }
    }

    const padding = step.padding || 8
    
    switch (step.position) {
      case 'top':
        return {
          bottom: `${window.innerHeight - highlightRect.top + padding}px`,
          left: `${highlightRect.left + highlightRect.width / 2}px`,
          transform: 'translateX(-50%)',
        }
      case 'bottom':
        return {
          top: `${highlightRect.bottom + padding}px`,
          left: `${highlightRect.left + highlightRect.width / 2}px`,
          transform: 'translateX(-50%)',
        }
      case 'left':
        return {
          top: `${highlightRect.top + highlightRect.height / 2}px`,
          right: `${window.innerWidth - highlightRect.left + padding}px`,
          transform: 'translateY(-50%)',
        }
      case 'right':
        return {
          top: `${highlightRect.top + highlightRect.height / 2}px`,
          left: `${highlightRect.right + padding}px`,
          transform: 'translateY(-50%)',
        }
      default:
        return {}
    }
  }

  return (
    <div className={`onboarding-guide-overlay ${isAnimating ? 'animate-in' : ''}`}>
      {/* 半透明遮罩 */}
      <div 
        className="guide-mask"
        style={{
          clipPath: highlightRect 
            ? `polygon(
                0 0, 100% 0, 100% 100%, 0 100%, 0 ${highlightRect.top}px,
                ${highlightRect.left}px ${highlightRect.top}px,
                ${highlightRect.left}px ${highlightRect.bottom}px,
                ${highlightRect.right}px ${highlightRect.bottom}px,
                ${highlightRect.right}px ${highlightRect.top}px,
                0 ${highlightRect.top}px
              )`
            : 'none',
        }}
      />
      
      {/* 高亮边框 */}
      {highlightRect && (
        <div
          className="guide-highlight"
          style={{
            top: `${highlightRect.top - 4}px`,
            left: `${highlightRect.left - 4}px`,
            width: `${highlightRect.width + 8}px`,
            height: `${highlightRect.height + 8}px`,
          }}
        />
      )}
      
      {/* 提示气泡 */}
      <div className="guide-bubble" style={getBubbleStyle()}>
        <div className="guide-bubble-cat"><Icon name="cat" size={26} /></div>
        <div className="guide-bubble-content">
          <h3 className="guide-bubble-title">{step.title}</h3>
          <p className="guide-bubble-desc">{step.description}</p>
        </div>
        <div className="guide-bubble-actions">
          <button className="guide-skip-btn" onClick={handleSkip}>
            跳过
          </button>
          <button className="guide-next-btn" onClick={handleNext}>
            {currentStep < GUIDE_STEPS.length - 1 ? '下一步' : '完成'}
          </button>
        </div>
        {/* 进度指示器 */}
        <div className="guide-progress">
          {GUIDE_STEPS.map((_, idx) => (
            <span
              key={idx}
              className={`guide-dot ${idx === currentStep ? 'active' : ''} ${idx < currentStep ? 'done' : ''}`}
            />
          ))}
        </div>
        {/* 进度百分比 */}
        <div className="guide-progress-text">{Math.round(progress)}%</div>
      </div>
    </div>
  )
}
