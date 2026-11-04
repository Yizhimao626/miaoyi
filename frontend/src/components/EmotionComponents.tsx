import { useState, useEffect, useRef, useCallback } from 'react'
import type { EmotionalState } from '../types'

interface EmotionalAvatarProps {
  emotion: string
  valence?: number
  arousal?: number
  intimacy?: number
  trust?: number
  size?: number
  animated?: boolean
  showParticles?: boolean  // 是否显示情感粒子
  showLabel?: boolean  // 是否显示情感标签
}

const EMOTION_CONFIGS: Record<string, { emoji: string; bg: string; glow: string; label: string; particle: string[] }> = {
  joyful: { emoji: '😊', bg: 'linear-gradient(135deg, #FFD93D, #FF9F43)', glow: 'rgba(255, 217, 61, 0.6)', label: '开心', particle: ['✨', '🌟', '😊'] },
  excited: { emoji: '🤩', bg: 'linear-gradient(135deg, #FF6B6B, #FF8E53)', glow: 'rgba(255, 107, 107, 0.6)', label: '兴奋', particle: ['🎉', '✨', '💫'] },
  content: { emoji: '😌', bg: 'linear-gradient(135deg, #A8E6CF, #55D6BE)', glow: 'rgba(85, 214, 190, 0.5)', label: '满足', particle: ['💚', '🌸'] },
  love: { emoji: '🥰', bg: 'linear-gradient(135deg, #FFB8D9, #FF8FB1)', glow: 'rgba(255, 143, 177, 0.6)', label: '爱意', particle: ['💕', '💗', '🌸'] },
  sad: { emoji: '😢', bg: 'linear-gradient(135deg, #B8D4E3, #7EB0D9)', glow: 'rgba(126, 176, 217, 0.4)', label: '悲伤', particle: ['💧', '🌧️'] },
  angry: { emoji: '😠', bg: 'linear-gradient(135deg, #FF6B6B, #EE5A5A)', glow: 'rgba(255, 107, 107, 0.6)', label: '生气', particle: ['🔥', '💢'] },
  curious: { emoji: '🤔', bg: 'linear-gradient(135deg, #C3A6FF, #9B7FFF)', glow: 'rgba(155, 127, 255, 0.5)', label: '好奇', particle: ['💡', '❓'] },
  playful: { emoji: '😜', bg: 'linear-gradient(135deg, #FFD93D, #FF6B9D)', glow: 'rgba(255, 217, 61, 0.5)', label: '调皮', particle: ['🎈', '🎉', '✨'] },
  tender: { emoji: '🤗', bg: 'linear-gradient(135deg, #FFE5B4, #FFCBA4)', glow: 'rgba(255, 203, 164, 0.5)', label: '温柔', particle: ['💗', '🌸', '✨'] },
  default: { emoji: '🐱', bg: 'linear-gradient(135deg, #E8D5B7, #C4A77D)', glow: 'rgba(196, 167, 125, 0.4)', label: '默认', particle: [] },
}

/** 线性插值 */
function lerp(start: number, end: number, t: number): number {
  return start + (end - start) * t
}

export function EmotionalAvatar({
  emotion, 
  valence, 
  arousal, 
  intimacy, 
  trust,
  size = 64, 
  animated = true,
  showParticles = true,
  showLabel = true,
}: EmotionalAvatarProps) {
  const [displayEmotion, setDisplayEmotion] = useState(emotion)
  const [isTransitioning, setIsTransitioning] = useState(false)
  const [animatedValence, setAnimatedValence] = useState(0.3)
  const [animatedArousal, setAnimatedArousal] = useState(0.4)
  const [animatedIntimacy, setAnimatedIntimacy] = useState(0.5)
  const [animatedTrust, setAnimatedTrust] = useState(0.5)
  const [breathPhase, setBreathPhase] = useState(0)
  
  const prevEmotionRef = useRef(emotion)
  const prevDimsRef = useRef({ valence: 0.3, arousal: 0.4, intimacy: 0.5, trust: 0.5 })
  const animFrameRef = useRef<number | null>(null)
  const transitionStartRef = useRef<number>(0)
  const emotionChangeTimerRef = useRef<number | null>(null)
  const breathFrameRef = useRef<number | null>(null)
  const breathStartRef = useRef<number>(0)

  // 4维数值平滑过渡
  useEffect(() => {
    if (valence === undefined || arousal === undefined || intimacy === undefined || trust === undefined) {
      return
    }

    const startDims = { 
      valence: animatedValence, 
      arousal: animatedArousal, 
      intimacy: animatedIntimacy, 
      trust: animatedTrust 
    }
    const endDims = { valence, arousal, intimacy, trust }
    const duration = 600 // ms
    const startTime = performance.now()

    const animate = (now: number) => {
      const elapsed = now - startTime
      const progress = Math.min(1, elapsed / duration)
      // 使用 ease-out 曲线让过渡更自然
      const eased = 1 - Math.pow(1 - progress, 3)
      
      setAnimatedValence(lerp(startDims.valence, endDims.valence, eased))
      setAnimatedArousal(lerp(startDims.arousal, endDims.arousal, eased))
      setAnimatedIntimacy(lerp(startDims.intimacy, endDims.intimacy, eased))
      setAnimatedTrust(lerp(startDims.trust, endDims.trust, eased))
      
      if (progress < 1) {
        animFrameRef.current = requestAnimationFrame(animate)
      } else {
        prevDimsRef.current = endDims
      }
    }

    if (animFrameRef.current) {
      cancelAnimationFrame(animFrameRef.current)
    }
    animFrameRef.current = requestAnimationFrame(animate)

    return () => {
      if (animFrameRef.current) {
        cancelAnimationFrame(animFrameRef.current)
      }
    }
  }, [valence, arousal, intimacy, trust])

  // 情绪变化带动画过渡
  useEffect(() => {
    if (emotion !== prevEmotionRef.current) {
      setIsTransitioning(true)
      transitionStartRef.current = performance.now()

      // 快速阶段 (0-300ms)：快速切换emoji和颜色
      if (emotionChangeTimerRef.current) {
        clearTimeout(emotionChangeTimerRef.current)
      }

      // 第一阶段：显示新emoji（带弹跳动画）
      requestAnimationFrame(() => {
        setDisplayEmotion(emotion)
      })

      // 第二阶段：完成过渡
      emotionChangeTimerRef.current = window.setTimeout(() => {
        setIsTransitioning(false)
        prevEmotionRef.current = emotion
      }, 350)
    }
  }, [emotion])

  // 呼吸光晕动画 - 根据情绪状态调整呼吸频率和强度
  useEffect(() => {
    if (!animated) {
      if (breathFrameRef.current) {
        cancelAnimationFrame(breathFrameRef.current)
      }
      return
    }

    breathStartRef.current = performance.now()
    
    const animate = (now: number) => {
      const elapsed = now - breathStartRef.current
      // 呼吸周期：唤醒度高时呼吸更快
      const breathPeriod = 3000 - animatedArousal * 1500 // 1.5s ~ 3s
      const phase = (elapsed % breathPeriod) / breathPeriod
      // 使用正弦函数产生平滑的呼吸效果
      const breathValue = Math.sin(phase * Math.PI * 2)
      setBreathPhase(breathValue)
      breathFrameRef.current = requestAnimationFrame(animate)
    }

    breathFrameRef.current = requestAnimationFrame(animate)
    return () => {
      if (breathFrameRef.current) {
        cancelAnimationFrame(breathFrameRef.current)
      }
    }
  }, [animated, animatedArousal])

  const config = EMOTION_CONFIGS[displayEmotion] || EMOTION_CONFIGS.default
  
  // 根据4维数值微调外观
  const adjustedGlow = animatedValence > 0 
    ? config.glow 
    : `rgba(150, 150, 200, 0.4)`
  const scaleBase = 1 + animatedArousal * 0.08
  const scale = isTransitioning ? scaleBase * 1.1 : scaleBase

  // 呼吸光晕效果
  const breathIntensity = animated ? (breathPhase + 1) / 2 : 0.5 // 0 ~ 1
  const breathGlowSize = animated 
    ? size * (0.3 + animatedValence * 0.3 + breathIntensity * 0.4)
    : size * 0.2
  const breathOpacity = animated 
    ? 0.3 + breathIntensity * 0.4 + animatedIntimacy * 0.2
    : 0.4

  return (
    <div
      className="emotional-avatar-wrapper"
      style={{
        position: 'relative',
        display: 'inline-block',
      }}
    >
      {/* 外层呼吸光晕 */}
      {animated && (
        <>
          <div
            className="breath-halo outer"
            style={{
              position: 'absolute',
              top: '50%',
              left: '50%',
              transform: 'translate(-50%, -50%)',
              width: size + breathGlowSize * 2,
              height: size + breathGlowSize * 2,
              borderRadius: '50%',
              background: `radial-gradient(circle, ${adjustedGlow} 0%, transparent 70%)`,
              opacity: breathOpacity * 0.6,
              transition: 'opacity 0.3s ease',
              pointerEvents: 'none',
              zIndex: -1,
            }}
          />
          <div
            className="breath-halo inner"
            style={{
              position: 'absolute',
              top: '50%',
              left: '50%',
              transform: 'translate(-50%, -50%)',
              width: size + breathGlowSize,
              height: size + breathGlowSize,
              borderRadius: '50%',
              background: `radial-gradient(circle, ${config.glow} 0%, transparent 60%)`,
              opacity: breathOpacity * 0.8,
              pointerEvents: 'none',
              zIndex: -1,
            }}
          />
        </>
      )}
      <div
        className="emotional-avatar"
        style={{
          width: size,
          height: size,
          borderRadius: '50%',
          background: config.bg,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          boxShadow: animated 
            ? `0 0 ${size * (0.2 + animatedValence * 0.2)}px ${adjustedGlow}, 0 0 ${breathGlowSize * 0.5}px ${config.glow}`
            : '0 4px 12px rgba(0,0,0,0.1)',
          transition: 'background 0.4s cubic-bezier(0.4, 0, 0.2, 1), box-shadow 0.4s ease',
          transform: `scale(${scale})`,
        }}
      >
        <span
          className="avatar-emoji"
          style={{
            fontSize: size * 0.5,
            transition: 'transform 0.3s cubic-bezier(0.34, 1.56, 0.64, 1)',
            animation: animated && isTransitioning 
              ? 'bounce 0.4s cubic-bezier(0.34, 1.56, 0.64, 1)' 
              : animated && animatedArousal > 0.6 
                ? 'gentlePulse 2s ease-in-out infinite' 
                : animated 
                  ? `breathPulse ${3 - animatedArousal * 1.5}s ease-in-out infinite`
                  : 'none',
          }}
        >
          {config.emoji}
        </span>
      </div>
      
      {/* 情感粒子效果 */}
      {showParticles && animated && config.particle.length > 0 && (
        <div className="emotion-particles" style={{
          position: 'absolute',
          top: '50%',
          left: '50%',
          transform: 'translate(-50%, -50%)',
          pointerEvents: 'none',
          zIndex: 10,
        }}>
          {config.particle.map((p, i) => (
            <span
              key={`${displayEmotion}-${i}`}
              className="particle"
              style={{
                position: 'absolute',
                fontSize: size * 0.25,
                // 将 delay 合并进 animation 简写，避免 animation/animationDelay 混用导致 React 样式警告
                animation: `particleFloat${i % 3} ${2 - animatedValence}s ease-out ${i * 0.3}s infinite`,
                opacity: 0.8,
              }}
            >
              {p}
            </span>
          ))}
        </div>
      )}
      
      {/* 情感标签 */}
      {showLabel && (
        <div 
          className="emotion-label"
          style={{
            position: 'absolute',
            bottom: -24,
            left: '50%',
            transform: 'translateX(-50%)',
            fontSize: 12,
            fontWeight: 500,
            color: config.glow.replace(/rgba?\([^)]+\)/, config.glow.includes('rgb') ? config.glow : '#666'),
            background: 'rgba(255, 255, 255, 0.9)',
            padding: '2px 8px',
            borderRadius: 10,
            whiteSpace: 'nowrap',
            boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
            transition: 'all 0.3s ease',
          }}
        >
          {config.label}
        </div>
      )}
      
      <style>{`
        @keyframes bounce {
          0% { transform: scale(1) rotate(0deg); }
          30% { transform: scale(1.3) rotate(-5deg); }
          60% { transform: scale(0.9) rotate(3deg); }
          100% { transform: scale(1) rotate(0deg); }
        }
        @keyframes gentlePulse {
          0%, 100% { transform: scale(1); }
          50% { transform: scale(1.05); }
        }
        @keyframes breathPulse {
          0%, 100% { 
            transform: scale(1); 
            filter: brightness(1);
          }
          50% { 
            transform: scale(1.03); 
            filter: brightness(1.1);
          }
        }
        @keyframes particleFloat0 {
          0% { transform: translate(0, 0) scale(0.5); opacity: 0; }
          30% { opacity: 1; }
          100% { transform: translate(-30px, -40px) scale(1); opacity: 0; }
        }
        @keyframes particleFloat1 {
          0% { transform: translate(0, 0) scale(0.5); opacity: 0; }
          30% { opacity: 1; }
          100% { transform: translate(30px, -35px) scale(1); opacity: 0; }
        }
        @keyframes particleFloat2 {
          0% { transform: translate(0, 0) scale(0.5); opacity: 0; }
          30% { opacity: 1; }
          100% { transform: translate(0, -45px) scale(1.2); opacity: 0; }
        }
        .particle {
          will-change: transform, opacity;
        }
      `}</style>
    </div>
  )
}

interface EmotionIndicatorProps {
  valence: number
  arousal: number
  intimacy: number
  trust: number
}

export function EmotionIndicator({ valence, arousal, intimacy, trust }: EmotionIndicatorProps) {
  const [display, setDisplay] = useState({ valence, arousal, intimacy, trust })
  const rafRef = useRef<number | null>(null)
  const startRef = useRef({ valence, arousal, intimacy, trust })
  const startTimeRef = useRef(0)

  // 4维数值平滑动画
  useEffect(() => {
    startRef.current = { ...display }
    startTimeRef.current = performance.now()
    const duration = 400

    const animate = (now: number) => {
      const elapsed = now - startTimeRef.current
      const progress = Math.min(1, elapsed / duration)
      const eased = 1 - Math.pow(1 - progress, 2)
      
      setDisplay({
        valence: lerp(startRef.current.valence, valence, eased),
        arousal: lerp(startRef.current.arousal, arousal, eased),
        intimacy: lerp(startRef.current.intimacy, intimacy, eased),
        trust: lerp(startRef.current.trust, trust, eased),
      })
      
      if (progress < 1) {
        rafRef.current = requestAnimationFrame(animate)
      }
    }

    rafRef.current = requestAnimationFrame(animate)
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
    }
  }, [valence, arousal, intimacy, trust])

  const getEmotionName = useCallback(() => {
    if (display.valence > 0.5 && display.intimacy > 0.7) return '爱意'
    if (display.valence > 0.5 && display.arousal > 0.7) return '兴奋'
    if (display.valence > 0.3) return '开心'
    if (display.valence < -0.5) return '低落'
    if (display.arousal > 0.7) return '激动'
    return '平静'
  }, [display])

  const getBarColor = (value: number, positiveColor: string, negativeColor: string) => {
    return value >= 0 ? positiveColor : negativeColor
  }

  return (
    <div className="emotion-indicator" style={{
      display: 'flex',
      gap: '8px',
      padding: '8px 12px',
      background: 'rgba(255,255,255,0.9)',
      borderRadius: '20px',
      boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
      alignItems: 'center',
    }}>
      <div className="emotion-bar" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '2px' }}>
        <div className="bar-label" style={{ fontSize: '9px', color: '#666' }}>心情</div>
        <div style={{
          width: '50px',
          height: '8px',
          background: '#E8E8E8',
          borderRadius: '4px',
          overflow: 'hidden',
          position: 'relative',
        }}>
          <div style={{
            position: 'absolute',
            left: display.valence >= 0 ? '50%' : `${50 + display.valence * 50}%`,
            width: `${Math.abs(display.valence) * 50}%`,
            height: '100%',
            background: getBarColor(display.valence, '#4CAF50', '#f44336'),
            borderRadius: display.valence >= 0 ? '0 4px 4px 0' : '4px 0 0 4px',
            transition: 'all 0.4s cubic-bezier(0.4, 0, 0.2, 1)',
          }} />
          <div style={{
            position: 'absolute',
            left: '50%',
            top: '0',
            width: '1px',
            height: '100%',
            background: '#ccc',
          }} />
        </div>
      </div>
      <div className="emotion-bar" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '2px' }}>
        <div className="bar-label" style={{ fontSize: '9px', color: '#666' }}>活力</div>
        <div style={{
          width: `${display.arousal * 50 + 5}px`,
          height: '8px',
          background: '#FF9800',
          borderRadius: '4px',
          transition: 'all 0.4s cubic-bezier(0.4, 0, 0.2, 1)',
        }} />
      </div>
      <div className="emotion-bar" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '2px' }}>
        <div className="bar-label" style={{ fontSize: '9px', color: '#666' }}>亲密</div>
        <div style={{
          width: `${display.intimacy * 50 + 5}px`,
          height: '8px',
          background: '#E91E63',
          borderRadius: '4px',
          transition: 'all 0.4s cubic-bezier(0.4, 0, 0.2, 1)',
        }} />
      </div>
      <div className="emotion-bar" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '2px' }}>
        <div className="bar-label" style={{ fontSize: '9px', color: '#666' }}>信任</div>
        <div style={{
          width: `${display.trust * 50 + 5}px`,
          height: '8px',
          background: '#9C27B0',
          borderRadius: '4px',
          transition: 'all 0.4s cubic-bezier(0.4, 0, 0.2, 1)',
        }} />
      </div>
      <div style={{ fontSize: '11px', color: '#333', alignSelf: 'center', marginLeft: '6px', fontWeight: 500 }}>
        {getEmotionName()}
      </div>
    </div>
  )
}

interface ThinkingIndicatorProps {
  status: 'thinking' | 'searching' | 'verifying' | 'typing' | 'done'
  text?: string
}

export function ThinkingIndicator({ status, text }: ThinkingIndicatorProps) {
  const statusConfig: Record<string, { emoji: string; label: string; color: string; catPaws: boolean }> = {
    thinking: { emoji: '🤔', label: '思考中', color: '#9C27B0', catPaws: true },
    searching: { emoji: '🔍', label: '搜索中', color: '#2196F3', catPaws: true },
    verifying: { emoji: '✅', label: '核实中', color: '#4CAF50', catPaws: false },
    typing: { emoji: '⌨️', label: '输入中', color: '#FF9800', catPaws: true },
    done: { emoji: '✨', label: '完成', color: '#8BC34A', catPaws: false },
  }
  
  const config = statusConfig[status]
  
  return (
    <div className="thinking-indicator" style={{
      display: 'flex',
      alignItems: 'center',
      gap: '8px',
      padding: '8px 12px',
      background: `${config.color}20`,
      borderRadius: '20px',
      animation: status === 'done' ? 'fadeOut 0.3s ease' : 'pulse 1.5s ease infinite',
      position: 'relative',
      overflow: 'visible',
    }}>
      {/* 猫爪足迹动画 */}
      {config.catPaws && status !== 'done' && (
        <div className="cat-paws-container" style={{
          position: 'absolute',
          left: '-30px',
          top: '50%',
          transform: 'translateY(-50%)',
          display: 'flex',
          gap: '4px',
        }}>
          {[0, 1, 2].map(i => (
            <span
              key={i}
              className="cat-paw"
              style={{
                fontSize: '12px',
                opacity: 0,
                animation: `catPawWalk 1.2s ease-in-out ${i * 0.3}s infinite`,
                filter: `hue-rotate(${config.color === '#9C27B0' ? '280deg' : config.color === '#2196F3' ? '180deg' : '30deg'})`,
              }}
            >
              🐾
            </span>
          ))}
        </div>
      )}
      <span style={{ fontSize: '16px' }}>{config.emoji}</span>
      <span style={{ fontSize: '13px', color: config.color, fontWeight: 500 }}>
        {text || config.label}
      </span>
      {status !== 'done' && (
        <div style={{ display: 'flex', gap: '2px' }}>
          {[0, 1, 2].map(i => (
            <span
              key={i}
              style={{
                width: '4px',
                height: '4px',
                background: config.color,
                borderRadius: '50%',
                animation: `bounce 1.4s ease-in-out ${i * 0.2}s infinite`,
              }}
            />
          ))}
        </div>
      )}
      <style>{`
        @keyframes bounce {
          0%, 80%, 100% { transform: scale(0); }
          40% { transform: scale(1); }
        }
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.7; }
        }
        @keyframes fadeOut {
          from { opacity: 1; }
          to { opacity: 0; }
        }
        @keyframes catPawWalk {
          0% { 
            opacity: 0; 
            transform: translateX(-10px) translateY(0) rotate(-15deg);
          }
          20% { 
            opacity: 0.8; 
          }
          50% { 
            opacity: 0.6; 
            transform: translateX(0) translateY(-4px) rotate(0deg);
          }
          80% { 
            opacity: 0.4; 
          }
          100% { 
            opacity: 0; 
            transform: translateX(10px) translateY(0) rotate(15deg);
          }
        }
      `}</style>
    </div>
  )
}

interface IntimacyLevelProps {
  level: number
  showLabel?: boolean
}

export function IntimacyLevel({ level, showLabel = true }: IntimacyLevelProps) {
  const [displayLevel, setDisplayLevel] = useState(level)
  const rafRef = useRef<number | null>(null)
  const startRef = useRef(level)
  const startTsRef = useRef(0)

  useEffect(() => {
    startRef.current = displayLevel
    startTsRef.current = performance.now()
    const duration = 500

    const animate = (now: number) => {
      const elapsed = now - startTsRef.current
      const progress = Math.min(1, elapsed / duration)
      const eased = 1 - Math.pow(1 - progress, 3)
      setDisplayLevel(lerp(startRef.current, level, eased))
      
      if (progress < 1) {
        rafRef.current = requestAnimationFrame(animate)
      }
    }

    rafRef.current = requestAnimationFrame(animate)
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
    }
  }, [level])

  const levels = [
    { name: '陌生人', min: 0, max: 0.2, emoji: '👋' },
    { name: '认识', min: 0.2, max: 0.4, emoji: '😊' },
    { name: '朋友', min: 0.4, max: 0.6, emoji: '🤝' },
    { name: '好朋友', min: 0.6, max: 0.8, emoji: '💕' },
    { name: '挚友', min: 0.8, max: 0.95, emoji: '🥰' },
    { name: '灵魂伴侣', min: 0.95, max: 1.01, emoji: '💖' },
  ]
  
  const currentLevelInfo = levels.find(l => displayLevel >= l.min && displayLevel < l.max) || levels[0]
  const nextLevelInfo = levels.find(l => displayLevel >= l.max && l.max <= 1)
  
  return (
    <div className="intimacy-level" style={{
      display: 'flex',
      alignItems: 'center',
      gap: '8px',
      padding: '6px 12px',
      background: 'linear-gradient(135deg, #FFE5B420, #FFCBA420)',
      borderRadius: '16px',
      transition: 'all 0.5s ease',
    }}>
      <span style={{ 
        fontSize: '18px',
        transition: 'transform 0.5s cubic-bezier(0.34, 1.56, 0.64, 1)',
        transform: displayLevel !== level ? 'scale(1.2)' : 'scale(1)',
      }}>{currentLevelInfo.emoji}</span>
      {showLabel && (
        <span style={{ fontSize: '12px', color: '#666' }}>
          亲密度: {currentLevelInfo.name}
          {nextLevelInfo && displayLevel >= nextLevelInfo.min - 0.05 && (
            <span style={{ color: '#999', marginLeft: '4px' }}>
              ({Math.round(displayLevel * 100)}%)
            </span>
          )}
        </span>
      )}
    </div>
  )
}

interface SmallThoughtBubbleProps {
  thought: string
  position?: 'left' | 'right'
}

export function SmallThoughtBubble({ thought, position = 'left' }: SmallThoughtBubbleProps) {
  if (!thought) return null
  
  return (
    <div className="small-thought" style={{
      position: 'relative',
      padding: '8px 16px',
      background: '#FFF9E6',
      borderRadius: '12px',
      fontSize: '12px',
      color: '#8B7355',
      fontStyle: 'italic',
      maxWidth: '60%',
      marginBottom: '8px',
      animation: 'fadeInUp 0.4s cubic-bezier(0.34, 1.56, 0.64, 1)',
      ...(position === 'right' ? { marginLeft: 'auto' } : {}),
    }}>
      {thought}
      <style>{`
        @keyframes fadeInUp {
          from {
            opacity: 0;
            transform: translateY(10px) scale(0.95);
          }
          to {
            opacity: 1;
            transform: translateY(0) scale(1);
          }
        }
      `}</style>
    </div>
  )
}

/** 情感气泡组件 - 动态显示情感变化 */
interface EmotionBubbleProps {
  state: EmotionalState
  className?: string
}

export function EmotionBubble({ state, className = '' }: EmotionBubbleProps) {
  const [displayValence, setDisplayValence] = useState(state.valence)
  const [displayArousal, setDisplayArousal] = useState(state.arousal)
  const rafRef = useRef<number | null>(null)
  const startRef = useRef({ valence: state.valence, arousal: state.arousal })
  const startTsRef = useRef(0)

  useEffect(() => {
    startRef.current = { valence: displayValence, arousal: displayArousal }
    startTsRef.current = performance.now()
    const duration = 500

    const animate = (now: number) => {
      const elapsed = now - startTsRef.current
      const progress = Math.min(1, elapsed / duration)
      const eased = 1 - Math.pow(1 - progress, 3)
      
      setDisplayValence(lerp(startRef.current.valence, state.valence, eased))
      setDisplayArousal(lerp(startRef.current.arousal, state.arousal, eased))
      
      if (progress < 1) {
        rafRef.current = requestAnimationFrame(animate)
      }
    }

    rafRef.current = requestAnimationFrame(animate)
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
    }
  }, [state.valence, state.arousal])

  const config = EMOTION_CONFIGS[state.dominant_emotion || 'content'] || EMOTION_CONFIGS.content

  // 根据情感计算气泡动画
  const floatIntensity = displayArousal * 20 + 10
  const shakeAmount = Math.max(0, displayArousal - 0.5) * 5

  return (
    <div 
      className={`emotion-bubble ${className}`}
      style={{
        position: 'absolute',
        top: '10px',
        right: '10px',
        width: '32px',
        height: '32px',
        borderRadius: '50%',
        background: config.bg,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        boxShadow: `0 0 ${floatIntensity}px ${config.glow}`,
        animation: `floatBubble ${3 + displayArousal}s ease-in-out infinite`,
        transform: `rotate(${shakeAmount - 2.5}deg)`,
        transition: 'all 0.5s cubic-bezier(0.4, 0, 0.2, 1)',
        zIndex: 10,
      }}
      title={`${config.label} (心情${displayValence.toFixed(2)}, 活力${displayArousal.toFixed(2)})`}
    >
      <span style={{ fontSize: '16px' }}>{config.emoji}</span>
      <style>{`
        @keyframes floatBubble {
          0%, 100% { transform: translateY(0px); }
          50% { transform: translateY(-${floatIntensity / 4}px); }
        }
      `}</style>
    </div>
  )
}
