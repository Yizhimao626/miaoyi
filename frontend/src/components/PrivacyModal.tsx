import { useState } from 'react'
import { setPrivacyAgreed } from '../utils/storage'
import { Icon } from './icons'

interface PrivacyModalProps {
  isOpen: boolean
  onClose: () => void
  onAgree: () => void
}

type DocType = 'privacy' | 'terms' | null

export const PRIVACY_TEXT = `隐私政策

欢迎使用喵艺！我们非常重视您的隐私保护。

一、信息收集
1. 您主动提供的昵称、头像等信息，用于个性化服务；
2. 您的聊天记录，仅用于提供对话、记忆功能；
3. 设备信息（如设备型号），用于技术诊断与安全防护。

二、信息使用
1. 您的聊天记录仅用于为您提供更好的服务体验；
2. 我们不会向任何第三方出售您的个人信息；
3. 您的记忆数据仅保存在您自己的设备与服务器。

三、信息存储
1. 数据存储于加密的服务器数据库中；
2. 您可以随时在「设置」中清除聊天记录与记忆数据。

四、您的权利
1. 您可以随时删除自己的账号与所有数据；
2. 您可以拒绝提供非必要信息。

如有疑问，可通过 App 内反馈功能联系我们。`

export const TERMS_TEXT = `用户协议

欢迎使用喵艺！请仔细阅读以下条款。

一、服务说明
1. 喵艺是一个 AI 陪伴应用，提供的对话内容由人工智能生成；
2. 所有 AI 生成内容仅供娱乐与参考，不构成任何专业建议。

二、使用规范
1. 您承诺不利用本服务从事违法活动；
2. 您理解 AI 回复可能存在不准确之处，请理性看待；
3. 禁止恶意攻击、滥用服务、干扰他人使用。

三、知识产权
1. 应用内所有内容（除用户上传外）归开发者所有；
2. 未经许可不得复制、传播应用内素材。

四、免责声明
1. AI 生成内容不代表开发者观点；
2. 因不可抗力导致的服务中断，开发者不承担责任。

继续使用即表示您同意以上条款。`

export function PrivacyModal({ isOpen, onClose, onAgree }: PrivacyModalProps) {
  const [doc, setDoc] = useState<DocType>(null)

  const handleAgree = () => {
    setPrivacyAgreed(true)
    onAgree()
  }

  const handleReject = () => {
    onClose()
  }

  if (!isOpen) return null

  // 【APK 修复】旧版 window.open('/privacy') 在 Capacitor WebView 中会打开
  // 不存在的 https://localhost/privacy → 白屏错误。改为站内弹窗展示文档。
  if (doc) {
    return (
      <div className="privacy-modal-overlay show">
        <div className="privacy-modal doc-modal">
          <h3 className="privacy-modal-title">
            {doc === 'privacy'
              ? <><Icon name="cat" size={20} />隐私政策</>
              : <><Icon name="clipboard" size={20} />用户协议</>}
          </h3>
          <div className="privacy-doc-content">
            <pre className="privacy-doc-pre">{doc === 'privacy' ? PRIVACY_TEXT : TERMS_TEXT}</pre>
          </div>
          <button className="privacy-btn accept btn-press" onClick={() => setDoc(null)}>
            返回
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="privacy-modal-overlay show">
      <div className="privacy-modal">
        <h3 className="privacy-modal-title"><Icon name="cat" size={20} />隐私政策</h3>
        <div className="privacy-modal-content">
          <p>欢迎使用喵艺！我们非常重视您的隐私保护。</p>
          <p>在使用本服务前，请您仔细阅读我们的隐私政策。我们承诺：</p>
          <ul>
            <li>• 您的聊天记录仅用于提供更好的服务体验</li>
            <li>• 我们不会向第三方分享您的个人信息</li>
            <li>• 您可以随时删除自己的记忆数据</li>
          </ul>
          <p>继续使用即表示您同意我们的隐私政策和用户协议。</p>
        </div>
        <div className="privacy-modal-links">
          <button className="privacy-link btn-press" onClick={() => setDoc('privacy')}>
            隐私政策
          </button>
          <button className="privacy-link btn-press" onClick={() => setDoc('terms')}>
            用户协议
          </button>
        </div>
        <div className="privacy-modal-actions">
          <button className="privacy-btn reject btn-press" onClick={handleReject}>
            暂不同意
          </button>
          <button className="privacy-btn accept btn-press" onClick={handleAgree}>
            同意
          </button>
        </div>
      </div>
    </div>
  )
}
