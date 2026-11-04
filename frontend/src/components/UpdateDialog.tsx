import type { AppVersionInfo } from '../utils/api'
import { getBaseUrl } from '../utils/config'
import { Icon } from './icons'

interface UpdateDialogProps {
  info: AppVersionInfo
  onLater: () => void
}

function formatSize(bytes: number): string {
  if (!bytes) return ''
  return bytes >= 1024 * 1024
    ? `${(bytes / 1024 / 1024).toFixed(1)} MB`
    : `${Math.round(bytes / 1024)} KB`
}

export function UpdateDialog({ info, onLater }: UpdateDialogProps) {
  const forced = Boolean(info.force_update)
  // 【P2修复】Android WebView 里 window.location.origin 是 https://localhost，
  // 相对路径的 download_url 拼上去必然失败。回退顺序：后端域名（从 API
  // 基地址剥离 /api 后缀）→ window.location.origin（浏览器场景仍可用）。
  const backendOrigin = (() => {
    try {
      const base = getBaseUrl()
      if (/^https?:\/\//.test(base)) return base.replace(/\/api\/?$/, '')
    } catch { /* ignore */ }
    return window.location.origin
  })()
  const url = info.download_url.startsWith('http')
    ? info.download_url
    : `${backendOrigin}${info.download_url}`

  const handleUpdate = () => {
    // 用带 download 属性的链接触发下载：
    // - 普通浏览器：直接下载，当前页不跳走
    // - Capacitor WebView：由原生 DownloadListener 接管，调起系统浏览器下载
    const a = document.createElement('a')
    a.href = url
    a.download = 'miaoyi-latest.apk'
    a.rel = 'noopener'
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
  }

  const notes = Array.isArray(info.release_notes) ? info.release_notes : []

  return (
    <div className={`update-overlay${forced ? ' forced' : ''}`}>
      <div className="update-dialog" role="dialog" aria-modal="true">
        <div className="update-hero">
          <span className="update-hero-emoji"><Icon name="cat" size={40} /></span>
          <span className="update-hero-paw"><Icon name="paw" size={16} /></span>
        </div>

        <div className="update-title">{info.update_title || '发现新版本'}</div>
        <div className="update-version">
          v{info.latest_version_name}
          {info.file_size > 0 && <span className="update-size"> · {formatSize(info.file_size)}</span>}
        </div>

        {notes.length > 0 && (
          <ul className="update-notes">
            {notes.map((note, i) => (
              <li key={i}>{note}</li>
            ))}
          </ul>
        )}

        <button type="button" className="update-btn-now btn-press" onClick={handleUpdate}>
          立即更新
        </button>

        {forced ? (
          <div className="update-force-tip">当前版本过低，更新后才能继续陪伴哦～</div>
        ) : (
          <button type="button" className="update-btn-later" onClick={onLater}>
            以后再说
          </button>
        )}
      </div>
    </div>
  )
}
