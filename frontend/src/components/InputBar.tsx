import { useState, useRef, useEffect, useMemo } from 'react'
import type { Message } from '../types'
import { useCallStore } from '../store/callStore'
import { uploadChatImage, getBaseUrl } from '../utils/api'
import { useVoiceRecorder } from '../hooks/useVoiceRecorder'
import { useHaptic } from '../hooks/useOnlineStatus'
import { MIAOYI_STICKERS, buildStickerText, stickerImgSrc } from '../utils/sticker'
import { Icon } from './icons'

const EMOJI_CATEGORIES = [
  { name: '表情', icons: ['😀', '😂', '🤣', '😍', '🥰', '😘', '😊', '😎', '🤓', '😋', '😜', '🤪', '🤑', '🤗', '🤭', '🤫'] },
  { name: '爱心', icons: ['❤️', '🧡', '💛', '💚', '💙', '💜', '🖤', '🤍', '🤎', '❣️', '💕', '💞', '💓', '💗', '💖', '💘'] },
  { name: '动物', icons: ['🐱', '🐶', '🐭', '🐹', '🐰', '🦊', '🐻', '🐼', '🐨', '🐯', '🦁', '🐮', '🐷', '🐸', '🐵', '🐔'] },
  { name: '食物', icons: ['🍎', '🍊', '🍋', '🍇', '🍓', '🍑', '🍒', '🥝', '🍅', '🥑', '🍕', '🍔', '🍟', '🌭', '🍿', '🍩'] },
  { name: '庆祝', icons: ['🎉', '🎊', '🎁', '🎈', '🎂', '✨', '🌟', '💫', '🎆', '🎇', '🎀', '🏆', '🎊', '🎉', '🤩', '🥳'] },
]

// 【对标微信 2026-10-01】最近使用的 emoji：持久化到 localStorage，作为面板第一个分类
const RECENT_EMOJI_KEY = 'miaoyi_recent_emojis'
const RECENT_EMOJI_MAX = 16

/**
 * 输入区组件 —— 从 ChatView 拆分（2026-08，重构第 2 步，继 CallDialButton 之后）
 * 包含：文本输入框 + 表情/自制表情/喵艺表情包/语音录音 四个面板 + 语音识别/图片文件上传。
 * replyingTo 状态保留在 ChatView（消息右键菜单"回复"也会设置它），通过 props 传入。
 */
interface InputBarProps {
  onSendMessage: (text: string, replyTo?: string, imageUrl?: string) => void
  onShowToast: (message: string, type?: 'success' | 'error' | 'warning') => void
  onUpdateMessages?: (messages: Message[] | ((prev: Message[]) => Message[])) => void
  /** 快速连发检测的记录函数（指示器 UI 仍在 ChatView 渲染） */
  recordSend: () => void
  /** 正在回复的消息（状态在 ChatView） */
  replyingTo: Message | null
  onCancelReply: () => void
  /** 发送成功后回调：ChatView 做强制滚底等簿记 */
  onSent?: () => void
}

export function InputBar({
  onSendMessage,
  onShowToast,
  onUpdateMessages,
  recordSend,
  replyingTo,
  onCancelReply,
  onSent,
}: InputBarProps) {
  const [inputText, setInputText] = useState('')
  const [showEmojiPanel, setShowEmojiPanel] = useState(false)
  const [showStickerPanel, setShowStickerPanel] = useState(false)
  const [showMiaoStickerPanel, setShowMiaoStickerPanel] = useState(false)
  // 【微信化】"+"更多功能面板
  const [showPlusPanel, setShowPlusPanel] = useState(false)
  const [selectedEmojiCategory, setSelectedEmojiCategory] = useState(0)
  const [customStickerText, setCustomStickerText] = useState('')
  const [customStickerBg, setCustomStickerBg] = useState('pink')
  const [showVoiceRecorder, setShowVoiceRecorder] = useState(false)
  const [isListening, setIsListening] = useState(false)
  const [inputPlaceholder, setInputPlaceholder] = useState('输入消息...')
  // 【对标微信 2026-10-01】最近使用的 emoji
  const [recentEmojis, setRecentEmojis] = useState<string[]>(() => {
    try { return JSON.parse(localStorage.getItem(RECENT_EMOJI_KEY) || '[]') } catch { return [] }
  })
  // 「最近」分类插到最前，无记录时不显示
  const emojiCategories = useMemo(() => {
    const cats = [...EMOJI_CATEGORIES]
    if (recentEmojis.length > 0) {
      cats.unshift({ name: '最近', icons: recentEmojis.slice(0, RECENT_EMOJI_MAX) })
    }
    return cats
  }, [recentEmojis])

  const placeholders = [
    '输入消息...',
    '试试点一下"举个栗子"喵~',
    '问我什么都可以，喵艺不抓人',
    '喵~ 有什么想聊的？',
    '告诉我你的想法，我在听~',
    '今天心情怎么样呀？',
    '试试发送"喵喵叫"有惊喜！',
  ]
  
  useEffect(() => {
    const interval = setInterval(() => {
      const randomIndex = Math.floor(Math.random() * placeholders.length)
      setInputPlaceholder(placeholders[randomIndex])
    }, 8000)
    return () => clearInterval(interval)
  }, [])

  const { isRecording, recordingTime, startRecording, stopRecording, cancelRecording } = useVoiceRecorder()
  const haptic = useHaptic()

  const inputRef = useRef<HTMLTextAreaElement>(null)
  // 【语音输入 Bug 修复】避免闭包陈旧值问题：
  // 旧代码 recognition.onend 里用的 inputText 是 render 时捕获的闭包值，
  // setState 是异步批处理的，onresult 里 setInputText(transcript) 后，
  // onend 里的 inputText 仍可能是空（React 没重渲染）→ 导致"语音说完但不发送"。
  // 修复：用 ref 保存实时 transcript，onend 时从 ref 取值
  const speechTranscriptRef = useRef<string>('')

  const MAX_MESSAGE_LENGTH = 2000

  /**
   * 发送消息。
   *
   * 参数说明：
   * - 无参调用（点击发送按钮 / Enter 键）：读 React state.inputText
   * - forceText 是 string（语音识别 onend 时直接传文本）：忽略 state 直接发送
   * - forceText 是 Event/其他（比如 onClick 事件对象）：忽略，走 state 分支
   */
  const handleSend = (forceText?: string | unknown) => {
    const rawText = typeof forceText === 'string' ? forceText : inputText
    const text = rawText.trim()
    if (!text) return
    
    if (text.length > MAX_MESSAGE_LENGTH) {
      onShowToast(`消息过长，请限制在${MAX_MESSAGE_LENGTH}字以内`, 'error')
      haptic('warning')
      return
    }
    
    // 检测快速连续消息（使用 hook）
    recordSend()
    
    // 发送消息震动反馈
    haptic('light')
    
    // 直接发送用户原始消息，由后端处理上下文总结
    // 不在前端注入额外的总结指令，避免污染用户消息
    onSendMessage(text, replyingTo?.id)

    setInputText('')
    onCancelReply()

    if (inputRef.current) {
      inputRef.current.style.height = 'auto'
    }

    // 发送后的滚动簿记（justSentRef/新消息计数/强制滚底）在 ChatView，
    // 通过 onSent 回调触发 —— InputBar 不持有滚动状态。
    onSent?.()
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInputText(e.target.value)
    e.target.style.height = 'auto'
    e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px'
  }

  const handleSpeechToggle = () => {
    const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition

    if (!SpeechRecognition) {
      onShowToast('您的浏览器不支持语音识别', 'error')
      return
    }

    if (isListening) {
      setIsListening(false)
      return
    }

    // 每次开启新的识别会话先清空缓存
    speechTranscriptRef.current = ''

    const recognition = new SpeechRecognition()
    recognition.lang = 'zh-CN'
    recognition.interimResults = true
    recognition.maxAlternatives = 1

    recognition.onstart = () => {
      setIsListening(true)
    }

    recognition.onresult = (event: any) => {
      const transcript = Array.from(event.results)
        .map((result: any) => result[0].transcript)
        .join('')
      // 【语音输入 Bug 修复】同步写入 ref，确保 onend 里能拿到最新值
      speechTranscriptRef.current = transcript
      setInputText(transcript)
    }

    recognition.onend = () => {
      setIsListening(false)
      // 【语音输入 Bug 修复】从 ref 取值而不是从闭包的 inputText 取值
      // 闭包里的 inputText 可能因为 setState 异步批处理还是旧值（空）
      const finalText = (speechTranscriptRef.current || '').trim()
      speechTranscriptRef.current = ''
      if (finalText) {
        // 先让输入框显示识别结果（提升交互反馈）
        setInputText(finalText)
        // 【关键修复】直接把文本传给 handleSend(forceText)，
        // 不等 React 批处理 flush setState 再读 inputText。
        handleSend(finalText)
      }
    }

    recognition.onerror = (event: any) => {
      setIsListening(false)
      speechTranscriptRef.current = ''
      if (event.error === 'not-allowed') {
        onShowToast('请允许麦克风权限', 'error')
      } else {
        onShowToast(`语音识别失败: ${event.error}`, 'error')
      }
    }

    recognition.start()
  }

  const handleEmojiSelect = (emoji: string) => {
    setInputText(prev => prev + emoji)
    // 【审查修复·对标微信】选择后面板保持打开，支持连续点选；点面板外遮罩收起
    // 记录最近使用：去重置顶，最多 16 个
    setRecentEmojis(prev => {
      const next = [emoji, ...prev.filter(e => e !== emoji)].slice(0, RECENT_EMOJI_MAX)
      try { localStorage.setItem(RECENT_EMOJI_KEY, JSON.stringify(next)) } catch { /* 存储满时忽略 */ }
      return next
    })
  }

  // 【对标微信 2026-10-01】点击面板外区域收起所有面板
  const closeAllPanels = () => {
    setShowEmojiPanel(false)
    setShowStickerPanel(false)
    setShowMiaoStickerPanel(false)
    setShowVoiceRecorder(false)
    setShowPlusPanel(false)
  }

  const handleToggleEmojiPanel = () => {
    setShowEmojiPanel(!showEmojiPanel)
    setShowStickerPanel(false)
    setShowVoiceRecorder(false)
    setShowMiaoStickerPanel(false)
    setShowPlusPanel(false)
  }

  const handleToggleStickerPanel = () => {
    setShowStickerPanel(!showStickerPanel)
    setShowEmojiPanel(false)
    setShowVoiceRecorder(false)
    setShowMiaoStickerPanel(false)
    setShowPlusPanel(false)
  }

  // 【方案一】喵艺官方表情包：开关面板 + 发送贴纸
  const handleToggleMiaoStickerPanel = () => {
    setShowMiaoStickerPanel(!showMiaoStickerPanel)
    setShowEmojiPanel(false)
    setShowStickerPanel(false)
    setShowVoiceRecorder(false)
    setShowPlusPanel(false)
  }

  // 【微信化】"+"更多功能面板：图片/文件/表情制作/语音输入/拨号
  const handleTogglePlusPanel = () => {
    setShowPlusPanel(!showPlusPanel)
    setShowEmojiPanel(false)
    setShowStickerPanel(false)
    setShowMiaoStickerPanel(false)
    setShowVoiceRecorder(false)
  }

  const handleSendSticker = (id: string) => {
    onSendMessage(buildStickerText(id))
    setShowMiaoStickerPanel(false)
  }

  const handleCreateSticker = () => {
    if (!customStickerText.trim()) return
    const sticker = `[${customStickerText.trim()}]`
    setInputText(prev => prev + sticker)
    setCustomStickerText('')
    setShowStickerPanel(false)
    onShowToast('表情已添加', 'success')
  }

  const handleImageClick = () => {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = 'image/jpeg,image/png,image/webp,image/gif'
    input.onchange = async (e) => {
      const file = (e.target as HTMLInputElement).files?.[0]
      if (!file) return

      onShowToast('正在上传图片...', 'warning')
      try {
        const result = await uploadChatImage(file)
        if (result.code === 200 && result.data?.url) {
          // 【视觉能力】图片进入聊天流：AI 通过视觉模型看图并自然回应
          onShowToast('图片已发送，喵艺正在看～', 'success')
          onSendMessage('', undefined, result.data.url)
        } else {
          onShowToast(result.message || '图片上传失败', 'error')
        }
      } catch (error) {
        onShowToast('图片上传失败，请重试', 'error')
      }
    }
    input.click()
  }

  // 【多模态·对标星黎 demo 5.0】相机"看一眼"：实时取景拍照 → 走视觉链路。
  // getUserMedia 需要安全上下文：App 内（capacitor://）与 https 可用，http 网页端会优雅报错
  const handleCameraClick = async () => {
    setShowPlusPanel(false)
    if (!navigator.mediaDevices?.getUserMedia) {
      onShowToast('当前环境不支持相机（App 内或 HTTPS 网页可用）', 'error')
      return
    }
    let stream: MediaStream | null = null
    try {
      onShowToast('正在打开相机...', 'warning')
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'environment' },
        audio: false,
      })
      const video = document.createElement('video')
      video.srcObject = stream
      video.playsInline = true
      video.muted = true
      await video.play()
      await new Promise((r) => setTimeout(r, 500)) // 等首帧稳定

      const canvas = document.createElement('canvas')
      canvas.width = video.videoWidth || 640
      canvas.height = video.videoHeight || 480
      canvas.getContext('2d')?.drawImage(video, 0, 0, canvas.width, canvas.height)
      stream.getTracks().forEach((t) => t.stop())
      stream = null

      const blob: Blob | null = await new Promise((res) => canvas.toBlob(res, 'image/jpeg', 0.85))
      if (!blob) throw new Error('拍照失败')
      const file = new File([blob], `camera_${Date.now()}.jpg`, { type: 'image/jpeg' })

      onShowToast('拍照完成，喵艺正在看～', 'success')
      const result = await uploadChatImage(file)
      if (result.code === 200 && result.data?.url) {
        onSendMessage('', undefined, result.data.url)
      } else {
        onShowToast(result.message || '上传失败', 'error')
      }
    } catch (error) {
      stream?.getTracks().forEach((t) => t.stop())
      onShowToast(`相机不可用：${(error as Error).message || '权限被拒绝'}`, 'error')
    }
  }

  const handleFileClick = () => {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = '.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.zip,.rar,.txt'
    input.onchange = async (e) => {
      const file = (e.target as HTMLInputElement).files?.[0]
      if (!file) return
      
      onShowToast(`正在上传文件: ${file.name}`, 'warning')
      
      const formData = new FormData()
      formData.append('file', file)
      
      try {
        const response = await fetch(`${getBaseUrl()}/upload/file`, {
          method: 'POST',
          body: formData,
        })
        
        const result = await response.json()
        if (result.code === 200) {
          const fileMessage: Message = {
            id: Date.now().toString(),
            role: 'user',
            text: '',
            status: 'done',
            timestamp: Date.now(),
            fileName: file.name,
            fileSize: file.size,
            fileType: file.type,
            fileUrl: result.data.url,
            msgType: 'file',
          }
          onUpdateMessages?.(prev => [...prev, fileMessage])
          onShowToast('文件上传成功！', 'success')
        } else {
          onShowToast(result.message || '上传失败', 'error')
        }
      } catch (error) {
        onShowToast('上传失败，请重试', 'error')
      }
    }
    input.click()
  }

  const handleToggleVoiceRecorder = async () => {
    setShowPlusPanel(false)
    if (isRecording) {
      const result = await stopRecording()
      if (result) {
        const { audioBlob, duration } = result
        if (duration < 1) {
          onShowToast('录音时间太短', 'warning')
        } else {
          onShowToast(`已录音 ${duration} 秒，正在发送...`, 'success')
          // 【#5 修复】先上传音频到服务器换取 URL，再以 done 状态追加消息。
          // 旧版仅用本地 objectURL 展示、无任何上传代码，消息永久卡 sending，
          // 冷启动后被恢复逻辑改判 failed
          try {
            const formData = new FormData()
            const audioFile = new File(
              [audioBlob],
              `voice_${Date.now()}.webm`,
              { type: audioBlob.type || 'audio/webm' }
            )
            formData.append('file', audioFile)
            const uploadResp = await fetch(`${getBaseUrl()}/upload/file`, {
              method: 'POST',
              body: formData,
            })
            const uploadResult = await uploadResp.json()
            if (uploadResult.code !== 200) {
              throw new Error(uploadResult.message || '上传失败')
            }
            const voiceMessage: Message = {
              id: Date.now().toString(),
              role: 'user',
              text: '',
              status: 'done',
              timestamp: Date.now(),
              audioUrl: uploadResult.data.url,
              voiceDuration: duration,
              msgType: 'voice',
            }
            onUpdateMessages?.(prev => [...prev, voiceMessage])
          } catch (error) {
            // 上传失败：以 failed 状态保留本地可播放的录音，而不是静默丢失
            const localUrl = URL.createObjectURL(audioBlob)
            const voiceMessage: Message = {
              id: Date.now().toString(),
              role: 'user',
              text: '',
              status: 'failed',
              timestamp: Date.now(),
              audioUrl: localUrl,
              voiceDuration: duration,
              msgType: 'voice',
            }
            onUpdateMessages?.(prev => [...prev, voiceMessage])
            onShowToast('语音上传失败，请重试', 'error')
          }
        }
      }
      setShowVoiceRecorder(false)
    } else {
      setShowVoiceRecorder(true)
      setShowEmojiPanel(false)
      setShowStickerPanel(false)
      try {
        await startRecording()
      } catch (error) {
        onShowToast((error as Error).message || '无法开始录音', 'error')
        setShowVoiceRecorder(false)
      }
    }
  }

  const handleCancelRecording = () => {
    cancelRecording()
    setShowVoiceRecorder(false)
  }

  const formatRecordingTime = (seconds: number) => {
    const mins = Math.floor(seconds / 60)
    const secs = seconds % 60
    return `${mins}:${secs.toString().padStart(2, '0')}`
  }

  return (
    <>
      {/* 【对标微信 2026-10-01】面板外点击遮罩：收起所有面板 */}
      {(showEmojiPanel || showStickerPanel || showMiaoStickerPanel || showPlusPanel) && (
        <div className="panel-overlay" onClick={closeAllPanels} />
      )}
      {/* 【定位锚点】面板与输入栏同容器，面板 bottom:100% 精确锚在输入栏上方；
          旧结构面板直接挂 chat-area，offsetParent 是 app-container，面板飞到屏幕顶部外 */}
      <div className="input-anchor">
      {showEmojiPanel && (
        <div className="emoji-panel">
          <div className="emoji-categories">
            {emojiCategories.map((cat, idx) => (
              <button
                key={cat.name}
                className={`emoji-category-btn ${selectedEmojiCategory === idx ? 'active' : ''}`}
                onClick={() => setSelectedEmojiCategory(idx)}
              >
                {cat.name}
              </button>
            ))}
          </div>
          <div className="emoji-grid">
            {emojiCategories[Math.min(selectedEmojiCategory, emojiCategories.length - 1)].icons.map((emoji, idx) => (
              <button
                key={`${emoji}-${idx}`}
                className="emoji-btn"
                onClick={() => handleEmojiSelect(emoji)}
              >
                {emoji}
              </button>
            ))}
          </div>
        </div>
      )}

      {showStickerPanel && (
        <div className="sticker-panel">
          <h3 className="sticker-panel-title">制作表情</h3>
          <input
            type="text"
            className="sticker-input"
            placeholder="输入表情文字..."
            value={customStickerText}
            onChange={(e) => setCustomStickerText(e.target.value)}
            maxLength={20}
          />
          <div className="sticker-colors">
            {['pink', 'blue', 'yellow', 'green', 'purple', 'orange'].map((color) => (
              <button
                key={color}
                className={`sticker-color-btn ${customStickerBg === color ? 'active' : ''}`}
                style={{ backgroundColor: color }}
                onClick={() => setCustomStickerBg(color)}
              />
            ))}
          </div>
          <button className="sticker-create-btn btn-press" onClick={handleCreateSticker}>
            <Icon name="sparkles" size={16} /> 创建表情
          </button>
        </div>
      )}

      {/* 【方案一】喵艺官方表情包面板 */}
      {showMiaoStickerPanel && (
        <div className="miao-sticker-panel">
          <div className="miao-sticker-panel-title"><Icon name="paw" size={16} /> 喵艺表情包</div>
          <div className="miao-sticker-grid">
            {MIAOYI_STICKERS.map((s) => (
              <button key={s.id} className="miao-sticker-cell btn-press" onClick={() => handleSendSticker(s.id)}>
                <img src={stickerImgSrc(s.id)} alt={s.label} className="miao-sticker-img" />
                <span className="miao-sticker-label">{s.label}</span>
              </button>
            ))}
          </div>
        </div>
      )}
      {showVoiceRecorder && (
        <div className="voice-recorder-panel">
          <div className="voice-recorder-timer">{formatRecordingTime(recordingTime)}</div>
          <div className="voice-recorder-actions">
            <button className="voice-cancel-btn btn-press" onClick={handleCancelRecording} aria-label="取消录音"><Icon name="x" size={18} /></button>
            <button className="voice-send-btn btn-press" onClick={handleToggleVoiceRecorder} aria-label="结束录音"><Icon name="check" size={18} /></button>
          </div>
        </div>
      )}

      {/* 【微信化】"+"更多功能面板：微信式图标网格 */}
      {showPlusPanel && (
        <div className="plus-panel">
          <div className="plus-grid">
            <button type="button" className="plus-item btn-press" onClick={handleImageClick}>
              <span className="plus-icon"><Icon name="image" size={24} /></span>
              <span className="plus-label">图片</span>
            </button>
            {/* 【多模态·对标星黎 demo 5.0】相机实时"看一眼" */}
            <button type="button" className="plus-item btn-press" onClick={handleCameraClick}>
              <span className="plus-icon"><Icon name="camera" size={24} /></span>
              <span className="plus-label">看一眼</span>
            </button>
            <button type="button" className="plus-item btn-press" onClick={handleFileClick}>
              <span className="plus-icon"><Icon name="paperclip" size={24} /></span>
              <span className="plus-label">文件</span>
            </button>
            <button type="button" className="plus-item btn-press" onClick={handleToggleStickerPanel}>
              <span className="plus-icon"><Icon name="palette" size={24} /></span>
              <span className="plus-label">制作表情</span>
            </button>
            <button type="button" className="plus-item btn-press" onClick={handleToggleMiaoStickerPanel}>
              <span className="plus-icon"><Icon name="cat" size={24} /></span>
              <span className="plus-label">喵艺表情</span>
            </button>
            <button
              type="button"
              className={`plus-item btn-press ${isListening ? 'active' : ''}`}
              onClick={handleSpeechToggle}
            >
              <span className="plus-icon"><Icon name="headphones" size={24} /></span>
              <span className="plus-label">语音输入</span>
            </button>
            <button
              type="button"
              className="plus-item btn-press"
              onClick={() => {
                // 与 CallDialButton 同逻辑：空闲发起、通话中挂断（避免 button 嵌套 button）
                const cs = useCallStore.getState()
                if (cs.status === 'idle') cs.startOutgoing()
                else cs.endCall()
                setShowPlusPanel(false)
              }}
            >
              <span className="plus-icon"><Icon name="phone" size={24} /></span>
              <span className="plus-label">打电话</span>
            </button>
          </div>
        </div>
      )}

      <div className="input-area">
        {replyingTo && (
          <div className="reply-bar">
            <span className="reply-icon"><Icon name="reply" size={16} /></span>
            <span className="reply-content">{replyingTo.text || (replyingTo.msgType === 'voice' ? '语音消息' : '消息')}</span>
            <button className="reply-cancel btn-press" onClick={onCancelReply} aria-label="取消回复"><Icon name="x" size={16} /></button>
          </div>
        )}

        <div className="input-bar">
          {/* 左：语音消息（微信式"按住说话"位） */}
          <button
            type="button"
            className={`tool-btn voice-tool btn-press ${isRecording ? 'recording' : ''}`}
            title="语音消息"
            onClick={handleToggleVoiceRecorder}
          >
            <Icon name="mic" size={22} />
          </button>
          <div className="message-input-wrapper">
            <textarea
              ref={inputRef}
              className="message-input"
              placeholder={replyingTo ? '回复消息...' : inputPlaceholder}
              value={inputText}
              onChange={handleInputChange}
              onKeyDown={handleKeyDown}
              maxLength={MAX_MESSAGE_LENGTH}
            />
            {inputText.length > 200 && (
              <span className={`char-count ${inputText.length > MAX_MESSAGE_LENGTH * 0.8 ? 'warning' : ''}`}>
                {inputText.length}/{MAX_MESSAGE_LENGTH}
              </span>
            )}
          </div>
          {/* 表情：始终保留 */}
          <button
            type="button"
            className={`tool-btn emoji-tool btn-press ${showEmojiPanel ? 'active' : ''}`}
            title="表情"
            onClick={handleToggleEmojiPanel}
          >
            <Icon name="smile" size={22} />
          </button>
          {/* 无文字：加号更多；有文字：发送 */}
          {inputText.trim() ? (
            <button type="button" className="send-btn btn-press active" onClick={handleSend} aria-label="发送">
              <Icon name="arrowUp" size={22} strokeWidth={2.2} />
            </button>
          ) : (
            <button
              type="button"
              className={`tool-btn plus-tool btn-press ${showPlusPanel ? 'active' : ''}`}
              title="更多功能"
              onClick={handleTogglePlusPanel}
              aria-label="更多功能"
            >
              <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                <line x1="12" y1="5" x2="12" y2="19" />
                <line x1="5" y1="12" x2="19" y2="12" />
              </svg>
            </button>
          )}
        </div>
      </div>
      </div>

    </>
  )
}
