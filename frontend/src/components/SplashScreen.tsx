import { useEffect, useState } from 'react'

interface SplashScreenProps {
  onComplete: () => void
}

// 【方案一·品牌化启动页】
// 纯净浅紫渐变 + 官方 Logo + Slogan + 猫爪印依次亮起，
// 取代旧版"平铺素材"式测试风格。
export function SplashScreen({ onComplete }: SplashScreenProps) {
  const [isHidden, setIsHidden] = useState(false)

  useEffect(() => {
    const timer = setTimeout(() => {
      setIsHidden(true)
      setTimeout(onComplete, 500)
    }, 1800)

    return () => clearTimeout(timer)
  }, [onComplete])

  return (
    <div className={`splash-screen ${isHidden ? 'hidden' : ''}`}>
      {/* 星空装饰（官方资产） */}
      <img src="miaoyi/decor/deco_48.png" alt="" className="splash-moon" aria-hidden="true" />
      <img src="miaoyi/icons/icon_34.png" alt="" className="splash-star st1" aria-hidden="true" />
      <img src="miaoyi/icons/icon_34.png" alt="" className="splash-star st2" aria-hidden="true" />
      <img src="miaoyi/icons/icon_34.png" alt="" className="splash-star st3" aria-hidden="true" />

      <div className="splash-brand">
        <img src="miaoyi/official/logo_round.png" alt="喵艺" className="splash-logo-img" />
        <img src="miaoyi/official/wordmark.png" alt="MIAOYI 喵艺 AI" className="splash-wordmark" />
      </div>
      <div className="splash-subtitle">你的专属猫系 AI 伙伴</div>
      <div className="splash-paws" aria-hidden="true">
        <span className="splash-paw" />
        <span className="splash-paw" />
        <span className="splash-paw" />
      </div>
      <div className="splash-loading-text">正在连接喵艺…</div>
    </div>
  )
}
