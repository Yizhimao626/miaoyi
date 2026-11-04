import { useRef, useState, useEffect, useMemo, memo } from 'react'
import type { ReactNode } from 'react'
import type { Message, EmotionalState } from '../types'
import { synthesizeVoice } from '../utils/api'
import { getBaseUrl } from '../utils/config'
import { getMute, escapeHtml } from '../utils/storage'
import { renderMarkdown } from '../utils/markdown'
import { useTypewriter } from '../hooks/useTypewriter'
import { useMessageSwipe } from '../hooks/useMessageSwipe'
import { getAvatarFromEmotionalState } from '../utils/emotionAvatar'
import { parseSticker, stickerImgSrc, extractStickers } from '../utils/sticker'
import { cleanTextForSpeech, resolveAudioUrl } from '../services/tts'
import { getAccessToken } from '../utils/auth'
import { Icon } from './icons'

interface MessageBubbleProps {
  message: Message
  onLongPress: (message: Message, x: number, y: number) => void
  onReply?: (message: Message) => void
  isVisible?: boolean
  onContinue?: (message: Message) => void
  emotionalState?: EmotionalState
  onRetry?: (message: Message) => void
  animationDelay?: number
  isNew?: boolean
  searchQuery?: string
  isHighlighted?: boolean
  /** 【对标微信 2026-10-01】引用回复：根据 replyTo id 查找原消息，用于展示引用内容 */
  getMessageById?: (id: string) => Message | undefined
  /** 点击引用块跳转到原消息 */
  onJumpToMessage?: (id: string) => void
  /** 【对标豆包/猫箱】点击推荐选项：把短选项作为用户消息发送 */
  onPickOption?: (text: string) => void
}

const typedMessageIds = new Set<string>()

/** 【APK 修复】后端返回的相对媒体路径（/uploads/xxx）在 Capacitor App 中会打到
 *  手机本地 localhost，统一拼上后端地址；http(s)/blob:/data: 开头的不动。
 *  【安全修复】后端已移除裸 StaticFiles 挂载，/uploads/ /exports/ 改走带鉴权的
 *  /api/files/* 路由（支持 ?token= 查询参数），此处同步重写。 */
function resolveMediaUrl(url?: string): string {
  if (!url) return ''
  if (/^(https?:|blob:|data:)/.test(url)) return url
  let path = url.startsWith('/') ? url : `/${url}`
  let isFileRoute = false
  if (path.startsWith('/uploads/')) { path = `/api/files/uploads/${path.slice('/uploads/'.length)}`; isFileRoute = true }
  else if (path.startsWith('/exports/')) { path = `/api/files/exports/${path.slice('/exports/'.length)}`; isFileRoute = true }
  const base = getBaseUrl()
  if (base.startsWith('http')) {
    path = `${base}${path}`
  }
  // 【鉴权】/api/files/* 路由需要令牌，<img>/<a> 标签带不了 Authorization 头，改用 ?token= 查询参数
  if (isFileRoute) {
    const tk = getAccessToken()
    if (tk) path += `${path.includes('?') ? '&' : '?'}token=${encodeURIComponent(tk)}`
  }
  return path
}

const getEmotionStyle = (emotionalState?: EmotionalState) => {
  if (!emotionalState) return { tone: 'normal', speed: 1, emoji: '' }
  
  const { dominant_emotion } = emotionalState
  
  const styles: Record<string, { tone: string; speed: number; emoji: string }> = {
    joyful: { tone: 'happy', speed: 1.2, emoji: '😄' },
    excited: { tone: 'excited', speed: 1.3, emoji: '🤩' },
    content: { tone: 'calm', speed: 1, emoji: '😊' },
    love: { tone: 'tender', speed: 0.85, emoji: '🥰' },
    sad: { tone: 'sad', speed: 0.8, emoji: '😢' },
    angry: { tone: 'angry', speed: 1.2, emoji: '😠' },
    curious: { tone: 'curious', speed: 1, emoji: '🤔' },
    playful: { tone: 'playful', speed: 1.2, emoji: '😜' },
    tender: { tone: 'tender', speed: 0.85, emoji: '🤗' },
    proud: { tone: 'confident', speed: 1.05, emoji: '😎' },
    grateful: { tone: 'warm', speed: 0.95, emoji: '🙏' },
    bored: { tone: 'flat', speed: 0.7, emoji: '😴' },
    lonely: { tone: 'low', speed: 0.75, emoji: '🥺' },
    default: { tone: 'normal', speed: 1, emoji: '' },
  }
  
  return styles[dominant_emotion || 'default'] || styles.default
}

// 高亮搜索词
const highlightSearchTerm = (text: string, searchTerm: string): ReactNode[] => {
  if (!searchTerm.trim()) return [text]
  
  const lowerText = text.toLowerCase()
  const lowerSearch = searchTerm.toLowerCase()
  const results: ReactNode[] = []
  let lastIndex = 0
  let index = lowerText.indexOf(lowerSearch)
  let keyCounter = 0
  
  while (index !== -1) {
    // 添加匹配前的文本
    if (index > lastIndex) {
      results.push(text.slice(lastIndex, index))
    }
    // 添加高亮的匹配文本
    results.push(
      <mark key={`hl-${keyCounter++}`} className="search-highlight">
        {text.slice(index, index + searchTerm.length)}
      </mark>
    )
    lastIndex = index + searchTerm.length
    index = lowerText.indexOf(lowerSearch, lastIndex)
  }
  
  // 添加最后剩余的文本
  if (lastIndex < text.length) {
    results.push(text.slice(lastIndex))
  }
  
  return results
}

// 【性能优化】memo 化：聊天页状态变化（搜索输入/右键菜单/打字指示器）时跳过未变气泡的重渲染
export const MessageBubble = memo(function MessageBubble({ 
  message, 
  onLongPress, 
  onReply, 
  isVisible = true, 
  onContinue,
  emotionalState,
  onRetry,
  animationDelay = 0,
  isNew = false,
  searchQuery = '',
  isHighlighted = false,
  getMessageById,
  onJumpToMessage,
  onPickOption,
}: MessageBubbleProps) {
  const [isPlaying, setIsPlaying] = useState(false)
  const [imageLoaded, setImageLoaded] = useState(false)
  const [isHovered, setIsHovered] = useState(false)
  const [isPetted, setIsPetted] = useState(false)
  const [petParticles, setPetParticles] = useState<Array<{ id: number; x: number; y: number }>>([])
  const [showThoughtAnimation, setShowThoughtAnimation] = useState(false)
  const [isCollapsed, setIsCollapsed] = useState(true)
  const [previewImage, setPreviewImage] = useState<string | null>(null)
  const [voiceError, setVoiceError] = useState(false)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const bubbleRef = useRef<HTMLDivElement>(null)
  
  // 【P3 体验】AI 长消息折叠：超过阈值自动折叠，点击"展开"查看全文
  const COLLAPSE_THRESHOLD = 300
  const shouldCollapse = message.role === 'ai'
    && message.msgType === 'normal'
    && message.status === 'done'
    && (message.text?.length || 0) > COLLAPSE_THRESHOLD
    && isCollapsed
  
  const emotionStyle = getEmotionStyle(emotionalState)
  const typingSpeed = Math.max(10, 30 / emotionStyle.speed)
  
  // 滑动手势：左滑删除，右滑回复
  const swipe = useMessageSwipe({
    onSwipeLeft: () => onLongPress(message, 0, 0),  // 左滑触发菜单（含删除）
    onSwipeRight: () => onReply?.(message),          // 右滑触发回复
  })
  
  const { displayText, isTyping, startTyping, skipTyping, appendText } = useTypewriter({
    speed: typingSpeed,
    onComplete: () => {
      if (message.msgType === 'normal' && message.role === 'ai') {
        typedMessageIds.add(message.id)
      }
    }
  })

  // 【方案一】官方表情包：从正文提取 [sticker:xxx]，气泡显示干净文本
  const stickerInfo = extractStickers(message.text || '')

  // 【流式回复】chunk 增量经 appendText 追加（打字机效果由真实生成节奏驱动）
  const streamedLenRef = useRef(0)
  const wasStreamedRef = useRef(false)

  useEffect(() => {
    if (message.role === 'ai' && message.status === 'streaming' && message.msgType === 'normal') {
      wasStreamedRef.current = true
      const full = stickerInfo.cleanText || message.text || ''
      if (full.length > streamedLenRef.current) {
        appendText(full.slice(streamedLenRef.current))
        streamedLenRef.current = full.length
      }
    }
  }, [message.role, message.status, message.text, appendText, stickerInfo.cleanText])

  useEffect(() => {
    if (message.msgType === 'normal' && message.role === 'ai' && message.status === 'done') {
      // 曾流式显示过的消息：补齐最终文本与流式已显示部分的差集即可（不重打）
      if (wasStreamedRef.current) {
        const finalFull = stickerInfo.cleanText || message.text || ''
        if (finalFull.length > streamedLenRef.current) {
          appendText(finalFull.slice(streamedLenRef.current))
          streamedLenRef.current = finalFull.length
        }
        return
      }
      if (!displayText && !typedMessageIds.has(message.id)) {
        startTyping(stickerInfo.cleanText || '')
      }
    }
  }, [message.msgType, message.role, message.status, message.text, displayText, startTyping, appendText, stickerInfo.cleanText])
  
  useEffect(() => {
    if (!isVisible && isTyping) {
      skipTyping()
    }
  }, [isVisible, isTyping, skipTyping])

  useEffect(() => {
    if (!isTyping && (displayText === message.text || displayText === stickerInfo.cleanText) && message.text) {
      typedMessageIds.add(message.id)
    }
  }, [isTyping, displayText, message.text])

  useEffect(() => {
    if (message.smallThought) {
      setShowThoughtAnimation(true)
      const timer = setTimeout(() => {
        setShowThoughtAnimation(false)
      }, 3000)
      return () => clearTimeout(timer)
    }
  }, [message.smallThought])

  const finalText = message.role === 'user' ? stickerInfo.cleanText : (isTyping ? displayText : stickerInfo.cleanText)

  // 【对标豆包/猫箱】识别 AI 回复中的短选项列表（如「- 📝 写点东西」），
  // 从气泡文本中剥离，渲染为气泡下方可点的推荐 chips。
  // 约束：只在 2-5 个、去 emoji 后 2-8 个汉字的短列表生效，避免误伤正常 Markdown 清单。
  const optionParse = useMemo<{ text: string; options: string[] }>(() => {
    if (message.role !== 'ai' || isTyping) return { text: finalText, options: [] }
    const found: string[] = []
    const kept = finalText.split('\n').map((line) => {
      const m = line.match(/^\s*[-·•*]\s+(.+?)\s*$/)
      if (!m) return line
      const raw = m[1].trim()
      const plain = raw
        .replace(/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{2190}-\u{21FF}\u{FE0F}]/gu, '')
        .replace(/[（(].*?[)）]/g, '')
        .trim()
      if (plain.length >= 2 && plain.length <= 8) {
        found.push(raw)
        return ''
      }
      return line
    })
    if (found.length < 2 || found.length > 5) return { text: finalText, options: [] }
    const text = kept.join('\n').replace(/\n{3,}/g, '\n\n').replace(/^\n+/, '').trim()
    return { text, options: found }
  }, [finalText, message.role, isTyping])

  // 【过滤调试标签】smallThought 若为纯英文短词（如 "respond"、"thinking"）则是后端泄漏的调试标签，
  // 不是真正的内心OS。只显示包含中文且长度合理的思考内容。
  const validSmallThought = message.smallThought && /[\u4e00-\u9fa5]/.test(message.smallThought) && message.smallThought.length >= 2
    ? message.smallThought
    : null

  // 【对标参考抖音视频 · 微信式连发气泡】AI 一段话拆成多个独立短气泡，
  // 像真人半夜连发微信一样逐条蹦出。规则：先按换行拆，超过 22 字的行
  // 再按句末标点拆；最多 8 条。打字机流式期间不拆，完成后才拆。
  const segments = useMemo<string[]>(() => {
    if (message.role !== 'ai') return []
    // 轻度清理弱模型的语气词堆砌（如"呀，晚上好呀"、每条都"呀，"开头）
    const cleanSeg = (s: string, idx: number): string => {
      let t = s
      // 句首语气词与句尾语气词重复时，删句首："呀，晚上好呀" → "晚上好呀"
      t = t.replace(
        /^[呀啊哦嗯诶欸唔哎][，,、\s]+([^，。！？!?]*[呀啊哦嗯诶欸唔哎呢啦][。！？!?~～]?\s*$)/,
        '$1'
      )
      // 第 2 条起去掉无意义句首语气词
      if (idx > 0) t = t.replace(/^[呀啊哦嗯诶欸唔哎][，,、\s]+/, '')
      return t.trim()
    }
    const out: string[] = []
    optionParse.text.split('\n').forEach((line) => {
      const t = line.trim()
      if (!t) return
      if (t.length > 22) {
        const parts = t.match(/[^。！？!?…~～]+[。！？!?…~～]*\s*/g) || [t]
        parts.forEach((p) => { if (p.trim()) out.push(p.trim()) })
      } else {
        out.push(t)
      }
    })
    // 去掉弱模型机械夹带的独立"喵~"气泡（句内自然带出的"…喵~"保留）
    return out
      .map(cleanSeg)
      .filter((s) => s && !/^喵[。.！!~～…\s]*$/.test(s))
      .slice(0, 8)
  }, [optionParse.text, message.role])

  // 微信式连发：AI 回复完成、且能拆出 2 条以上时走多气泡布局
  const isMulti = message.role === 'ai' && !isTyping && segments.length >= 2

  const formatTime = (timestamp: number) => {
    const date = new Date(timestamp)
    const now = new Date()
    const hours = date.getHours().toString().padStart(2, '0')
    const minutes = date.getMinutes().toString().padStart(2, '0')
    // 【体验打磨】今天只显示时分；昨天显示"昨天 HH:MM"；更早显示"月/日 HH:MM"
    const isToday = date.toDateString() === now.toDateString()
    const yesterday = new Date(now)
    yesterday.setDate(now.getDate() - 1)
    const isYesterday = date.toDateString() === yesterday.toDateString()
    if (isToday) return `${hours}:${minutes}`
    if (isYesterday) return `昨天 ${hours}:${minutes}`
    return `${date.getMonth() + 1}/${date.getDate()} ${hours}:${minutes}`
  }

  const formatVoiceDuration = (seconds?: number) => {
    if (!seconds) return ''
    const mins = Math.floor(seconds / 60)
    const secs = seconds % 60
    return `${mins}:${secs.toString().padStart(2, '0')}`
  }

  const formatFileSize = (bytes?: number) => {
    if (!bytes) return ''
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  }

  // 【TTS 统一】文本预处理已移入 services/tts.ts（cleanTextForSpeech）
  const handleVoicePlay = async () => {
    if (getMute()) {
      return
    }
    
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current = null
      setIsPlaying(false)
      return
    }

    if (!message.text && !message.audioUrl) return

    setIsPlaying(true)
    setVoiceError(false)
    try {
      let audioUrl = message.audioUrl

      if (!audioUrl && message.text) {
        const cleanText = cleanTextForSpeech(message.text)
        const result = await synthesizeVoice(cleanText)
        audioUrl = result.data?.audio_url
      }

      if (!audioUrl) {
        setIsPlaying(false)
        return
      }

      const fullUrl = resolveAudioUrl(audioUrl)

      const audio = new Audio(fullUrl)
      audioRef.current = audio

      audio.onplay = () => setIsPlaying(true)
      audio.onpause = () => {
        setIsPlaying(false)
        audioRef.current = null
      }
      audio.onended = () => {
        setIsPlaying(false)
        audioRef.current = null
      }
      audio.onerror = () => {
        console.error('音频播放失败')
        setIsPlaying(false)
        setVoiceError(true) // 【产品化】"这次声音没能生成出来 [重新播放]"
        audioRef.current = null
      }

      audio.play().catch((e) => {
        console.error('音频播放失败:', e)
        setIsPlaying(false)
        setVoiceError(true)
        audioRef.current = null
      })
    } catch (e) {
      console.error('语音合成失败:', e)
      setIsPlaying(false)
      setVoiceError(true)
    }
  }

  const handleContextMenu = (e: React.MouseEvent) => {
    e.preventDefault()
    onLongPress(message, e.clientX, e.clientY)
  }

  const handleDoubleClick = () => {
    if (message.role !== 'ai' || message.status !== 'done') return
    
    setIsPetted(true)
    
    const newParticles = Array.from({ length: 8 }, (_, i) => ({
      id: Date.now() + i,
      x: Math.random() * 100,
      y: Math.random() * 100,
    }))
    setPetParticles(newParticles)
    
    const currentPetCount = parseInt(localStorage.getItem('petCount') || '0')
    localStorage.setItem('petCount', (currentPetCount + 1).toString())
    
    const achievements = JSON.parse(localStorage.getItem('achievements') || '[]')
    if (!achievements.includes('pet_cat')) {
      achievements.push('pet_cat')
      localStorage.setItem('achievements', JSON.stringify(achievements))
    }
    
    setTimeout(() => {
      setIsPetted(false)
      setPetParticles([])
    }, 1000)
  }

  const renderVoiceMessage = () => (
    <div 
      className={`message-bubble ${message.role} voice`} 
      onContextMenu={handleContextMenu}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      <div className={`voice-message-container ${isPlaying ? 'playing' : ''}`}>
        <button className={`voice-play-btn btn-press ${isPlaying ? 'playing' : ''}`} onClick={handleVoicePlay}>
          {isPlaying ? <Icon name="pause" size={15} /> : <Icon name="play" size={15} />}
        </button>
        <div className="voice-waveform">
          {[...Array(8)].map((_, i) => (
            <div key={i} className="voice-waveform-bar" style={{ animationDelay: `${i * 0.1}s` }} />
          ))}
        </div>
        <span className="voice-duration">{formatVoiceDuration(message.voiceDuration)}</span>
      </div>
    </div>
  )

  const renderImageMessage = () => (
    <div 
      className={`message-bubble ${message.role} image`} 
      onContextMenu={handleContextMenu}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      <div className="image-container">
        {!imageLoaded && (
          <div className="image-loading">
            <div className="loading-paws">
              <span className="paw"><Icon name="paw" size={18} /></span>
              <span className="paw"><Icon name="paw" size={18} /></span>
              <span className="paw"><Icon name="paw" size={18} /></span>
            </div>
          </div>
        )}
        <img
          src={resolveMediaUrl(message.imageUrl)}
          alt="图片消息"
          className={`image-content ${imageLoaded ? 'loaded' : 'hidden'}`}
          onLoad={() => setImageLoaded(true)}
          onClick={() => {
            const url = resolveMediaUrl(message.imageUrl)
            if (url) {
              // 【APK 修复】window.open 在 WebView 中可能白屏/无响应，改为全屏预览
              setPreviewImage(url)
            }
          }}
        />
      </div>
      {message.text && (
        <span className="message-text" dangerouslySetInnerHTML={{ __html: escapeHtml(message.text) }} />
      )}
    </div>
  )

  const renderFileMessage = () => (
    <div 
      className={`message-bubble ${message.role} file`} 
      onContextMenu={handleContextMenu}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      <div className="file-container">
        <div className="file-icon"><Icon name="paperclip" size={22} /></div>
        <div className="file-info">
          <span className="file-name">{message.fileName || '附件'}</span>
          <span className="file-size">{formatFileSize(message.fileSize)}</span>
        </div>
        {message.fileUrl && (
          <button className="file-download btn-press" onClick={() => {
            // 【APK 修复】window.open 在 WebView 中不可靠，改为触发下载
            const url = resolveMediaUrl(message.fileUrl)
            if (!url) return
            const a = document.createElement('a')
            a.href = url
            a.download = message.fileName || 'attachment'
            document.body.appendChild(a)
            a.click()
            document.body.removeChild(a)
          }}>
            下载
          </button>
        )}
      </div>
      {message.text && (
        <span className="message-text" dangerouslySetInnerHTML={{ __html: escapeHtml(message.text) }} />
      )}
    </div>
  )

  const renderThinkingMessage = () => (
    <div className="thinking-indicator">
      <span className="thinking-icon"><Icon name="messageSquare" size={15} /></span>
      <span className="thinking-text" dangerouslySetInnerHTML={{ __html: escapeHtml(message.text) }} />
    </div>
  )

  const renderTextMessage = () => (
    <div
      className={`message-item ${message.role} ${isNew ? 'new-message' : ''} ${isHighlighted ? 'search-highlighted' : ''} ${swipe.isSwiping ? 'swiping' : ''} ${swipe.showAction ? `swipe-${swipe.showAction}` : ''} ${optionParse.options.length > 0 ? 'has-suggestions' : ''}`}
      style={{
        // 注意：不要同时设置 animation 简写与 animationDelay 非简写，
        // 否则 React 会警告 "mixing shorthand and non-shorthand properties"。
        // 将 delay 直接合并进 animation 简写字符串（顺序：name duration timing-function delay iteration-count fill-mode）。
        animation: isNew ? `staggerMessageAppear 0.3s ease-out ${animationDelay}ms forwards` : undefined,
        opacity: isNew ? 0 : 1,
        transform: `translateX(${swipe.offset}px)`,
        transition: swipe.isSwiping ? 'none' : 'transform 0.3s cubic-bezier(0.34, 1.56, 0.64, 1)',
      }}
      {...swipe.touchHandlers}
    >
      {/* 滑动操作背景提示 */}
      {swipe.showAction && (
        <div className={`swipe-action-hint swipe-action-${swipe.showAction}`}>
          <span className="swipe-action-label">
            {swipe.showAction === 'reply'
              ? <><Icon name="reply" size={15} />回复</>
              : <><Icon name="trash" size={15} />更多</>}
          </span>
        </div>
      )}
      <div
        ref={bubbleRef}
        className={`message-bubble ${message.role} ${isHovered ? 'hovered' : ''} ${message.status === 'typing' ? 'generating' : ''} ${message.status === 'done' && !isTyping ? 'completed' : ''} ${message.status === 'failed' ? 'error' : ''} ${isPetted ? 'petted' : ''} ${message.msgType === 'proactive' ? 'proactive-msg' : ''} ${isMulti ? 'multi' : ''} emotion-${emotionStyle.tone}`}
        onContextMenu={handleContextMenu}
        onMouseEnter={() => setIsHovered(true)}
        onMouseLeave={() => setIsHovered(false)}
        onDoubleClick={handleDoubleClick}
        data-emotion={emotionStyle.tone}
      >
      {/* 【v2 精细化 · 对标豆包】移除猫耳装饰与重复的"喵艺"名字行：
          header 已展示身份，气泡内不再重复；AIGC 合规标识移至气泡底部弱化展示 */}
      
      {isPetted && message.role === 'ai' && (
        <div className="pet-particles">
          {petParticles.map((particle) => (
            <span
              key={particle.id}
              className="pet-particle"
              style={{
                left: `${particle.x}%`,
                top: `${particle.y}%`,
              }}
            >
              ✨
            </span>
          ))}
        </div>
      )}
      
      {message.status === 'typing' && (
        <div className="loading-indicator">
          <span className="breathing-dot" />
          <span className="loading-text">喵艺正在思考中...</span>
        </div>
      )}
      
      {message.status === 'typing' ? (
        <div className="loading-paws">
          <span className="paw"><Icon name="paw" size={18} /></span>
          <span className="paw"><Icon name="paw" size={18} /></span>
          <span className="paw"><Icon name="paw" size={18} /></span>
        </div>
      ) : isMulti ? (
        /* 【微信式连发】多个独立短气泡，逐条浮现 */
        <>
          {segments.map((seg, i) => (
            <span
              key={i}
              className="bubble-cell"
              style={{ animation: `bubbleCellIn 0.26s cubic-bezier(0.22,1,0.36,1) ${i * 0.4}s both` }}
              dangerouslySetInnerHTML={{ __html: renderMarkdown(seg) }}
            />
          ))}
          {stickerInfo.stickers.length > 0 && (
            <span className="bubble-stickers">
              {stickerInfo.stickers.map((sid) => (
                <img key={sid} src={stickerImgSrc(sid)} alt="喵艺贴纸" className="bubble-sticker-img" />
              ))}
            </span>
          )}
          <div className="multi-footer">
            <span className="ai-generated-badge" title="本条内容由人工智能生成">AI 生成</span>
            <button
              className={`voice-btn multi-voice btn-press ${isPlaying ? 'playing' : ''}`}
              onClick={handleVoicePlay}
              disabled={isPlaying || getMute()}
              title={getMute() ? '当前处于静音状态' : '点击播放语音'}
            >
              {isPlaying
                ? <Icon name="volume" size={15} />
                : getMute() ? <Icon name="volumeOff" size={15} /> : <Icon name="volume" size={15} />}
            </button>
          </div>
        </>
      ) : (
        <>
        <span className={`message-text ${message.role === 'ai' && isTyping ? 'typing' : ''} ${shouldCollapse ? 'collapsed-text' : ''}`}>
          {searchQuery.trim() && !isTyping
            ? highlightSearchTerm(shouldCollapse ? optionParse.text.slice(0, COLLAPSE_THRESHOLD) + '…' : optionParse.text, searchQuery)
            : <span dangerouslySetInnerHTML={{ __html: renderMarkdown(shouldCollapse ? optionParse.text.slice(0, COLLAPSE_THRESHOLD) + '…' : optionParse.text) }} />
          }
          {message.role === 'ai' && isTyping && <span className="typewriter-cursor" />}
        </span>
        {/* 【AIGC 合规】应用商店要求显著标识 AI 生成内容：弱化但保留 */}
        {message.role === 'ai' && message.msgType !== 'thinking' && message.msgType !== 'thought_bubble' && (
          <div className="bubble-ai-mark">
            <span className="ai-generated-badge" title="本条内容由人工智能生成">AI 生成</span>
          </div>
        )}
        {stickerInfo.stickers.length > 0 && !isTyping && (
          <span className="bubble-stickers">
            {stickerInfo.stickers.map((sid) => (
              <img key={sid} src={stickerImgSrc(sid)} alt="喵艺贴纸" className="bubble-sticker-img" />
            ))}
          </span>
        )}
        {shouldCollapse && (
          <button className="collapse-toggle-btn btn-press" onClick={() => setIsCollapsed(false)}>
            展开全文（{message.text?.length || 0} 字）<Icon name="chevronDown" size={15} />
          </button>
        )}
        </>
      )}

      {/* 【AIGC合规】多气泡布局由 multi-footer 承载标识，单气泡走下方旧控件 */}
      {!isMulti && message.role === 'ai' && message.msgType !== 'thinking' && message.msgType !== 'thought_bubble' && message.text && !isTyping && (
        <span className="ai-content-label" title="本回复由AI生成">AI</span>
      )}

      {!isMulti && message.role === 'ai' && message.msgType !== 'thinking' && message.msgType !== 'thought_bubble' && message.text && (
        <button
          className={`voice-btn btn-press ${isPlaying ? 'playing' : ''} ${emotionStyle.tone}`}
          onClick={handleVoicePlay}
          disabled={isPlaying || getMute()}
          title={getMute() ? '当前处于静音状态' : `点击播放语音(${emotionStyle.tone})`}
        >
          {isPlaying
            ? <Icon name="volume" size={15} />
            : getMute() ? <Icon name="volumeOff" size={15} /> : <Icon name="volume" size={15} />}
        </button>
      )}

      {/* 【产品化】语音失败提示：这次声音没能生成出来 [重新播放] */}
      {voiceError && (
        <span className="voice-error-hint">
          这次声音没能生成出来
          <button className="voice-error-retry btn-press" onClick={() => { setVoiceError(false); handleVoicePlay() }}>重新播放</button>
        </span>
      )}
      
      {message.role === 'ai' && message.status === 'done' && message.canContinue && onContinue && (
        <div className="action-buttons">
          <button
            className="action-btn btn-press"
            onClick={() => onContinue(message)}
          >
            继续 <Icon name="play" size={15} />
          </button>
        </div>
      )}
      
      {message.status === 'failed' && (
        <button
          className="retry-btn btn-press"
          onClick={() => {
            if (onRetry) {
              onRetry(message)
            }
          }}
        >
          重试 <Icon name="refresh" size={15} />
        </button>
      )}
      </div>

      {/* 【对标豆包/猫箱】推荐选项 chips：从回复中识别的短选项，点击直接发送 */}
      {optionParse.options.length > 0 && (
        <div className="msg-suggestions">
          {optionParse.options.map((opt) => {
            const plain = opt
              .replace(/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{2190}-\u{21FF}\u{FE0F}]/gu, '')
              .trim()
            return (
              <button
                key={opt}
                className="msg-suggestion btn-press"
                onClick={() => onPickOption?.(plain)}
              >
                {opt}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )

  const renderReplyIndicator = () => {
    if (!message.replyTo) return null
    // 【对标微信 2026-10-01】引用块升级：显示被引用者名字 + 内容摘要，点击跳转原消息。
    // 旧版只渲染一个"回复消息"占位条，看不出回复的是哪条。
    const quoted = getMessageById?.(message.replyTo)
    const quotedPreview = quoted
      ? (quoted.msgType === 'voice' ? '[语音]'
        : quoted.msgType === 'image' ? '[图片]'
        : quoted.msgType === 'file' ? `[文件] ${quoted.fileName || ''}`
        : (quoted.text || '').slice(0, 60) || '[消息]')
      : '原消息已删除'
    return (
      <div
        className={`reply-indicator ${quoted ? 'clickable' : ''}`}
        onClick={(e) => {
          e.stopPropagation()
          if (quoted) onJumpToMessage?.(quoted.id)
        }}
      >
        <span className="reply-quote-name">{quoted ? (quoted.role === 'user' ? '我' : '喵艺') : ''}</span>
        <span className="reply-quote-text">{quotedPreview}</span>
      </div>
    )
  }

  const renderMessageContent = () => {
    // 【方案一】官方表情包：识别 [sticker:xxx]，渲染为大图贴纸（不带气泡）
    const stickerId = parseSticker(message.text)
    if (stickerId && (message.msgType === 'normal' || !message.msgType)) {
      return (
        <div className="sticker-msg">
          <img src={stickerImgSrc(stickerId)} alt="喵艺贴纸" className="sticker-msg-img" />
        </div>
      )
    }
    switch (message.msgType) {
      case 'voice':
        return renderVoiceMessage()
      case 'image':
        return renderImageMessage()
      case 'file':
        return renderFileMessage()
      case 'thinking':
        return renderThinkingMessage()
      default:
        return renderTextMessage()
    }
  }

  return (
    <div className={`message-item ${message.role} ${message.msgType === 'thinking' ? 'thinking' : ''}`}>
      {message.msgType !== 'thinking' && (
        <div className="message-avatar">
          {message.role === 'ai'
            ? <img src={getAvatarFromEmotionalState(emotionalState)} alt="喵艺" className="message-avatar-img" />
            : <Icon name="user" size={18} />}
        </div>
      )}
      <div className="message-content">
        {message.role === 'ai' && message.msgType !== 'thinking' && validSmallThought && (
          <div
            className={`small-thought ${showThoughtAnimation ? 'animate-in' : ''}`}
            style={{
              animationDelay: '0.2s',
              opacity: showThoughtAnimation ? 1 : 0,
              transform: showThoughtAnimation ? 'translateY(0)' : 'translateY(-10px)'
            }}
            dangerouslySetInnerHTML={{ __html: escapeHtml(validSmallThought) }}
          />
        )}
        {renderReplyIndicator()}
        {/* 【第2步】气泡行重构：时间戳/状态移到气泡侧边，不再各占一整行 */}
        <div className="bubble-row">
          {renderMessageContent()}
          {message.msgType !== 'thinking' && (
            <div className="bubble-meta">
              <div className="message-time">{formatTime(message.timestamp)}</div>
              {message.role === 'user' && (
                <div className={`message-status ${message.status}`}>
                  {message.status === 'sending' && (
                    <div className="sending-dots">
                      <span></span><span></span><span></span>
                    </div>
                  )}
                  {message.status === 'failed' && (
                    <span className="status-error"><Icon name="alert" size={15} /> 发送失败</span>
                  )}
                  {message.status === 'done' && (
                    <span className="status-done"><Icon name="check" size={15} /></span>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
        {message.role === 'ai' && message.msgType !== 'thinking' && message.thought && (
          <div
            className="thought animate-in"
            style={{ animationDelay: '0.4s' }}
          >
            <Icon name="messageSquare" size={15} />
            <span dangerouslySetInnerHTML={{ __html: escapeHtml(message.thought) }} />
          </div>
        )}
      </div>

      {/* 【APK 修复】全屏图片预览（替代 window.open，WebView 兼容） */}
      {previewImage && (
        <div className="image-preview-overlay" onClick={() => setPreviewImage(null)}>
          <img src={previewImage} alt="图片预览" className="image-preview-full" onClick={e => e.stopPropagation()} />
          <button className="image-preview-close btn-press" onClick={() => setPreviewImage(null)}><Icon name="x" size={18} /></button>
        </div>
      )}
    </div>
  )
})