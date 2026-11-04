import { useState, useRef, useCallback } from 'react'
import type { Message } from '../types'
import { Icon } from './icons'

interface ShareCardProps {
  messages: Message[]
  onClose: () => void
  type?: 'chat' | 'achievement'
  achievementData?: {
    name: string
    description: string
    emoji: string
    unlockedAt: number
  }
}

// 生成简单的二维码占位图（实际项目中可接入真实二维码服务）
function generateQRCodePlaceholder(ctx: CanvasRenderingContext2D, x: number, y: number, size: number) {
  const cellSize = size / 25
  // 生成固定样式的二维码图案（模拟效果）
  ctx.fillStyle = '#333'
  
  // 三个角的定位标记
  const corners = [
    [0, 0],
    [size - 7 * cellSize, 0],
    [0, size - 7 * cellSize]
  ]
  
  corners.forEach(([cx, cy]) => {
    ctx.fillRect(x + cx, y + cy, 7 * cellSize, 7 * cellSize)
    ctx.fillStyle = '#fff'
    ctx.fillRect(x + cx + cellSize, y + cy + cellSize, 5 * cellSize, 5 * cellSize)
    ctx.fillStyle = '#333'
    ctx.fillRect(x + cx + 2 * cellSize, y + cy + 2 * cellSize, 3 * cellSize, 3 * cellSize)
  })
  
  // 随机填充其他区域（保持视觉效果）
  const seed = Date.now() % 100
  for (let i = 0; i < 25; i++) {
    for (let j = 0; j < 25; j++) {
      // 跳过定位标记区域
      if ((i < 8 && j < 8) || (i > 17 && j < 8) || (i < 8 && j > 17)) continue
      
      const hash = (i * 31 + j * 17 + seed) % 100
      if (hash < 45) {
        ctx.fillStyle = '#333'
        ctx.fillRect(x + i * cellSize, y + j * cellSize, cellSize, cellSize)
      }
    }
  }
}

// 绘制猫耳Logo
function drawCatLogo(ctx: CanvasRenderingContext2D, x: number, y: number, size: number) {
  ctx.save()
  
  // 圆形背景
  ctx.beginPath()
  ctx.arc(x + size / 2, y + size / 2, size / 2, 0, Math.PI * 2)
  ctx.fillStyle = 'rgba(255, 154, 118, 0.9)'
  ctx.fill()
  
  // 猫耳朵
  ctx.beginPath()
  ctx.moveTo(x + size * 0.2, y + size * 0.3)
  ctx.lineTo(x + size * 0.15, y + size * 0.1)
  ctx.lineTo(x + size * 0.35, y + size * 0.25)
  ctx.closePath()
  ctx.fillStyle = 'rgba(255, 154, 118, 0.9)'
  ctx.fill()
  
  ctx.beginPath()
  ctx.moveTo(x + size * 0.8, y + size * 0.3)
  ctx.lineTo(x + size * 0.85, y + size * 0.1)
  ctx.lineTo(x + size * 0.65, y + size * 0.25)
  ctx.closePath()
  ctx.fill()
  
  // 猫脸 emoji
  ctx.font = `${size * 0.5}px sans-serif`
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillText('🐱', x + size / 2, y + size / 2 + size * 0.05)
  
  ctx.restore()
}

export function ShareCard({ messages, onClose, type = 'chat', achievementData }: ShareCardProps) {
  const [isGenerating, setIsGenerating] = useState(false)
  const [generatedImage, setGeneratedImage] = useState<string | null>(null)
  const cardRef = useRef<HTMLDivElement>(null)

  const generateChatCard = useCallback(() => {
    const canvas = document.createElement('canvas')
    const ctx = canvas.getContext('2d')
    if (!ctx) return null

    const width = 750
    const padding = 40
    const cardHeight = 180
    const qrHeight = 180
    const height = Math.max(500, cardHeight + messages.length * 120 + qrHeight + 200)
    
    canvas.width = width
    canvas.height = height

    // 渐变背景
    const gradient = ctx.createLinearGradient(0, 0, width, height)
    gradient.addColorStop(0, '#FFF5F0')
    gradient.addColorStop(0.5, '#FFE8E0')
    gradient.addColorStop(1, '#FFE0D0')
    ctx.fillStyle = gradient
    ctx.fillRect(0, 0, width, height)

    // 装饰性猫爪水印
    ctx.save()
    ctx.globalAlpha = 0.05
    ctx.font = '300px sans-serif'
    ctx.fillStyle = '#FF9A76'
    ctx.fillText('🐾', width - 200, height / 2)
    ctx.restore()

    // 顶部卡片区域
    ctx.fillStyle = '#fff'
    ctx.shadowColor = 'rgba(255, 154, 118, 0.2)'
    ctx.shadowBlur = 20
    ctx.shadowOffsetY = 4
    ctx.beginPath()
    ctx.roundRect!(padding, padding, width - padding * 2, cardHeight, 24)
    ctx.fill()
    ctx.shadowBlur = 0
    ctx.shadowOffsetY = 0

    // 猫耳Logo
    drawCatLogo(ctx, padding + 20, padding + 20, 80)

    // 标题
    ctx.fillStyle = '#333'
    ctx.font = 'bold 28px "PingFang SC", "Microsoft YaHei", sans-serif'
    ctx.textAlign = 'left'
    ctx.fillText('喵艺 · Miaoyi', padding + 120, padding + 55)

    // 副标题
    ctx.fillStyle = '#999'
    ctx.font = '16px sans-serif'
    ctx.fillText('你的专属AI小伙伴', padding + 120, padding + 85)

    // 日期
    ctx.fillStyle = '#CCC'
    ctx.font = '14px sans-serif'
    ctx.fillText(`📅 ${new Date().toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' })}`, padding + 120, padding + 115)

    // 对话消息区域
    let y = padding + cardHeight + 30
    const maxMessages = Math.min(messages.length, 3)
    const displayMessages = messages.slice(-maxMessages)

    for (let i = 0; i < displayMessages.length; i++) {
      const msg = displayMessages[i]
      const msgHeight = 100
      
      // 消息背景
      const isUser = msg.role === 'user'
      const msgX = isUser ? width - padding - 320 : padding
      
      ctx.fillStyle = isUser ? '#FF9A76' : '#fff'
      ctx.shadowColor = 'rgba(0, 0, 0, 0.08)'
      ctx.shadowBlur = 8
      ctx.shadowOffsetY = 2
      ctx.beginPath()
      ctx.roundRect!(msgX, y, 320, msgHeight, 16)
      ctx.fill()
      ctx.shadowBlur = 0
      ctx.shadowOffsetY = 0

      // 消息角色标签
      ctx.fillStyle = isUser ? 'rgba(255,255,255,0.8)' : '#FF9A76'
      ctx.font = 'bold 12px sans-serif'
      ctx.fillText(isUser ? '💬 我' : '🐱 喵艺', msgX + 16, y + 24)

      // 消息内容
      ctx.fillStyle = isUser ? '#fff' : '#333'
      ctx.font = '15px sans-serif'
      const text = msg.text?.slice(0, 30) || ''
      ctx.fillText(text, msgX + 16, y + 50)
      
      if (msg.text && msg.text.length > 30) {
        ctx.fillText(msg.text.slice(30, 50) + '...', msgX + 16, y + 72)
      }

      y += msgHeight + 20
    }

    // 如果消息更多，显示省略号
    if (messages.length > maxMessages) {
      ctx.fillStyle = '#999'
      ctx.font = '14px sans-serif'
      ctx.textAlign = 'center'
      ctx.fillText(`... 还有 ${messages.length - maxMessages} 条消息`, width / 2, y + 10)
      y += 30
    }

    // 底部区域 - 二维码和引导语
    const bottomY = height - qrHeight - padding
    ctx.fillStyle = '#fff'
    ctx.shadowColor = 'rgba(255, 154, 118, 0.15)'
    ctx.shadowBlur = 16
    ctx.shadowOffsetY = 4
    ctx.beginPath()
    ctx.roundRect!(padding, bottomY, width - padding * 2, qrHeight, 20)
    ctx.fill()
    ctx.shadowBlur = 0
    ctx.shadowOffsetY = 0

    // 二维码区域
    const qrSize = 120
    const qrX = padding + 30
    const qrY = bottomY + (qrHeight - qrSize) / 2
    
    ctx.fillStyle = '#f5f5f5'
    ctx.beginPath()
    ctx.roundRect!(qrX - 10, qrY - 10, qrSize + 20, qrSize + 20, 12)
    ctx.fill()
    
    generateQRCodePlaceholder(ctx, qrX, qrY, qrSize)

    // 引导语
    ctx.fillStyle = '#333'
    ctx.font = 'bold 20px "PingFang SC", sans-serif'
    ctx.textAlign = 'left'
    ctx.fillText('🐾 扫码领养这只猫', qrX + qrSize + 40, bottomY + 60)

    ctx.fillStyle = '#999'
    ctx.font = '14px sans-serif'
    ctx.fillText('下载喵艺App，开启你的AI陪伴之旅', qrX + qrSize + 40, bottomY + 90)
    
    ctx.fillStyle = '#FF9A76'
    ctx.font = 'bold 16px sans-serif'
    ctx.fillText('✨ 免费体验 · 永久陪伴', qrX + qrSize + 40, bottomY + 120)

    // 底部水印
    ctx.fillStyle = 'rgba(255, 154, 118, 0.6)'
    ctx.font = '12px sans-serif'
    ctx.textAlign = 'center'
    ctx.fillText('Powered by 喵艺 Miaoyi · Made with ❤️', width / 2, height - 15)

    return canvas.toDataURL('image/png')
  }, [messages])

  const generateAchievementCard = useCallback(() => {
    if (!achievementData) return null
    
    const canvas = document.createElement('canvas')
    const ctx = canvas.getContext('2d')
    if (!ctx) return null

    const width = 600
    const height = 800
    
    canvas.width = width
    canvas.height = height

    // 渐变背景
    const gradient = ctx.createLinearGradient(0, 0, width, height)
    gradient.addColorStop(0, '#FFE4E1')
    gradient.addColorStop(0.5, '#FFDAB9')
    gradient.addColorStop(1, '#FFD4C4')
    ctx.fillStyle = gradient
    ctx.fillRect(0, 0, width, height)

    // 装饰性元素
    ctx.save()
    ctx.globalAlpha = 0.1
    ctx.font = '200px sans-serif'
    ctx.fillStyle = '#fff'
    ctx.fillText('⭐', 50, 150)
    ctx.fillText('✨', width - 150, 250)
    ctx.fillText('🌟', 80, height - 200)
    ctx.fillText('💫', width - 120, height - 150)
    ctx.restore()

    // 奖牌/成就图标大圆
    const iconSize = 180
    const centerX = width / 2
    const iconY = 180
    
    // 外圈发光
    ctx.save()
    ctx.shadowColor = 'rgba(255, 154, 118, 0.6)'
    ctx.shadowBlur = 40
    ctx.beginPath()
    ctx.arc(centerX, iconY + iconSize / 2, iconSize / 2 + 10, 0, Math.PI * 2)
    ctx.fillStyle = 'rgba(255, 255, 255, 0.9)'
    ctx.fill()
    ctx.restore()

    // 圆形背景
    ctx.beginPath()
    ctx.arc(centerX, iconY + iconSize / 2, iconSize / 2, 0, Math.PI * 2)
    const iconGradient = ctx.createRadialGradient(centerX, iconY + iconSize / 2, 0, centerX, iconY + iconSize / 2, iconSize / 2)
    iconGradient.addColorStop(0, '#FFD93D')
    iconGradient.addColorStop(1, '#FF9A76')
    ctx.fillStyle = iconGradient
    ctx.fill()

    // 成就Emoji
    ctx.font = '80px sans-serif'
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.fillText(achievementData.emoji, centerX, iconY + iconSize / 2)

    // 成就名称
    ctx.fillStyle = '#8B4513'
    ctx.font = 'bold 36px "PingFang SC", sans-serif'
    ctx.fillText(achievementData.name, centerX, iconY + iconSize + 60)

    // 成就描述
    ctx.fillStyle = '#A0522D'
    ctx.font = '20px sans-serif'
    ctx.fillText(achievementData.description, centerX, iconY + iconSize + 100)

    // 解锁时间
    const date = new Date(achievementData.unlockedAt)
    const dateStr = date.toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' })
    
    ctx.fillStyle = '#999'
    ctx.font = '16px sans-serif'
    ctx.fillText(`🏆 解锁于 ${dateStr}`, centerX, iconY + iconSize + 140)

    // 分享提示框
    const boxY = iconY + iconSize + 180
    ctx.fillStyle = 'rgba(255, 255, 255, 0.9)'
    ctx.shadowColor = 'rgba(0, 0, 0, 0.1)'
    ctx.shadowBlur = 20
    ctx.beginPath()
    ctx.roundRect!(80, boxY, width - 160, 120, 20)
    ctx.fill()
    ctx.shadowBlur = 0

    ctx.fillStyle = '#333'
    ctx.font = 'bold 18px sans-serif'
    ctx.fillText('🎉 恭喜解锁新成就', centerX, boxY + 40)

    ctx.fillStyle = '#666'
    ctx.font = '14px sans-serif'
    ctx.fillText('分享给好友，一起收集喵艺成就吧！', centerX, boxY + 70)

    // 底部品牌区
    drawCatLogo(ctx, centerX - 250, height - 80, 50)
    
    ctx.fillStyle = '#8B4513'
    ctx.font = 'bold 18px sans-serif'
    ctx.textAlign = 'left'
    ctx.fillText('喵艺 Miaoyi', centerX - 195, height - 45)
    
    ctx.fillStyle = '#A0522D'
    ctx.font = '12px sans-serif'
    ctx.fillText('AI 小伙伴 · 成就收集', centerX - 195, height - 25)

    // 右侧二维码
    const qrSize = 80
    generateQRCodePlaceholder(ctx, width - qrSize - 25, height - qrSize - 40, qrSize)

    return canvas.toDataURL('image/png')
  }, [achievementData])

  const handleGenerate = async () => {
    setIsGenerating(true)
    try {
      // 模拟生成延迟
      await new Promise(resolve => setTimeout(resolve, 500))
      
      const imageUrl = type === 'achievement' 
        ? generateAchievementCard() 
        : generateChatCard()
      
      if (imageUrl) {
        setGeneratedImage(imageUrl)
      }
    } catch (error) {
      console.error('生成分享卡片失败:', error)
    } finally {
      setIsGenerating(false)
    }
  }

  const handleDownload = () => {
    if (!generatedImage) return
    
    const link = document.createElement('a')
    link.href = generatedImage
    link.download = `miaoyi-${type}-${Date.now()}.png`
    link.click()
  }

  return (
    <div className="share-card-overlay" onClick={onClose}>
      <div className="share-card-container" ref={cardRef} onClick={(e) => e.stopPropagation()}>
        {/* 头部 */}
        <div className="share-card-header">
          <h3>{type === 'achievement' ? '分享成就' : '分享对话'}</h3>
          <button className="close-btn" onClick={onClose} aria-label="关闭"><Icon name="x" size={18} /></button>
        </div>

        {/* 图片预览/生成区域 */}
        <div className="share-card-body">
          {generatedImage ? (
            <div className="image-preview">
              <img src={generatedImage} alt="分享卡片" className="generated-image" />
              <div className="image-actions">
                <button className="btn-primary" onClick={handleDownload}>
                  <Icon name="download" size={16} /> 保存图片
                </button>
                <button className="btn-secondary" onClick={handleGenerate}>
                  <Icon name="refresh" size={16} /> 重新生成
                </button>
              </div>
            </div>
          ) : (
            <>
              {type === 'achievement' && achievementData ? (
                <div className="achievement-preview">
                  <div className="achievement-big-icon">{achievementData.emoji}</div>
                  <h2>{achievementData.name}</h2>
                  <p>{achievementData.description}</p>
                </div>
              ) : (
                <div className="chat-preview">
                  <div className="preview-header">
                    <span className="preview-icon"><Icon name="cat" size={20} /></span>
                    <span className="preview-title">喵艺</span>
                    <span className="preview-time">今日对话</span>
                  </div>
                  <div className="preview-messages">
                    {messages.slice(-3).map((msg, idx) => (
                      <div key={idx} className={`preview-message ${msg.role}`}>
                        <span className="preview-role">{msg.role === 'user' ? '我' : '喵艺'}</span>
                        <span className="preview-text">{msg.text?.slice(0, 40)}{msg.text && msg.text.length > 40 ? '...' : ''}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              
              <button 
                className="generate-btn btn-primary" 
                onClick={handleGenerate}
                disabled={isGenerating}
              >
                {isGenerating ? (
                  <>
                    <span className="cat-loading-paws">
                      <span><Icon name="paw" size={14} /></span><span><Icon name="paw" size={14} /></span><span><Icon name="paw" size={14} /></span>
                    </span>
                    喵爪绘制中...
                  </>
                ) : (
                  <>
                    <Icon name="sparkles" size={16} /> 生成精美分享图
                  </>
                )}
              </button>
            </>
          )}
        </div>

        {/* 提示文字 */}
        <div className="share-card-footer">
          <p>{type === 'achievement' ? '保存图片分享到朋友圈，炫耀你的成就吧！' : '保存图片分享给好友，让他们也领养一只喵～'}</p>
        </div>
      </div>
    </div>
  )
}
