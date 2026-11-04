interface BottomNavProps {
  currentTab: 'home' | 'chat' | 'memory' | 'miaoyi' | 'profile'
  onTabChange: (tab: 'home' | 'chat' | 'memory' | 'miaoyi' | 'profile') => void
}

/* 官方版图风格线性图标：stroke 跟随 currentColor，激活态自动变紫 */
function NavIcon({ tab }: { tab: string }) {
  const common = {
    className: 'nav-svg',
    viewBox: '0 0 24 24',
    fill: 'none',
    strokeWidth: 1.9,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
  }
  switch (tab) {
    case 'home':
      return (
        <svg {...common}>
          <path d="M3.5 10.5 12 3.5l8.5 7" />
          <path d="M5.5 9.5V20a1 1 0 0 0 1 1H10v-5.5h4V21h3.5a1 1 0 0 0 1-1V9.5" />
        </svg>
      )
    case 'memory':
      return (
        <svg {...common}>
          <path d="M12 5a4 4 0 0 0-4 4c-1.8.4-3 1.8-3 3.6 0 1.5.9 2.9 2.2 3.5-.1.4-.2.9-.2 1.3A3.6 3.6 0 0 0 10.6 21c.6 0 1-.4 1-1V6.8A3.8 3.8 0 0 0 12 5Z" />
          <path d="M12 5a4 4 0 0 1 4 4c1.8.4 3 1.8 3 3.6 0 1.5-.9 2.9-2.2 3.5.1.4.2.9.2 1.3A3.6 3.6 0 0 1 13.4 21c-.6 0-1-.4-1-1V6.8A3.8 3.8 0 0 1 12 5Z" />
        </svg>
      )
    case 'miaoyi':
      return (
        <svg {...common}>
          <path d="M4.5 9.5 3 4l5 2a7.5 7.5 0 0 1 8 0l5-2-1.5 5.5" />
          <path d="M20.5 12.5a8.5 7 0 1 1-17 0 8.5 7 0 0 1 17 0Z" />
          <path d="M9 13.5c.4.9 1.6 1.5 3 1.5s2.6-.6 3-1.5" />
          <path d="M9.2 10.4h.01M14.8 10.4h.01" strokeWidth="2.4" />
        </svg>
      )
    case 'profile':
      return (
        <svg {...common}>
          <circle cx="12" cy="8" r="3.6" />
          <path d="M5 20c.8-3.4 3.6-5.4 7-5.4s6.2 2 7 5.4" />
        </svg>
      )
    default:
      return null
  }
}

export function BottomNav({ currentTab, onTabChange }: BottomNavProps) {
  const tabs = [
    { id: 'home' as const, label: '首页' },
    { id: 'chat' as const, label: '聊天' },
    { id: 'memory' as const, label: '记忆' },
    { id: 'miaoyi' as const, label: '喵艺' },
    { id: 'profile' as const, label: '我的' },
  ]

  return (
    <div className="bottom-nav miaoyi-nav">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          className={`nav-item btn-press ${currentTab === tab.id ? 'active' : ''} ${tab.id === 'chat' ? 'nav-chat-item' : ''}`}
          onClick={() => onTabChange(tab.id)}
        >
          {tab.id === 'chat' ? (
            /* 中央凸起猫头按钮（官方紫色圆形猫脸资产） */
            <span className="nav-chat-fab">
              <img src="miaoyi/icons/icon_11.png" alt="" className="nav-fab-img" />
            </span>
          ) : (
            <NavIcon tab={tab.id} />
          )}
          <span className="nav-label">{tab.label}</span>
          {currentTab === tab.id && tab.id !== 'chat' && (
            <span className="nav-indicator"></span>
          )}
        </button>
      ))}
    </div>
  )
}
