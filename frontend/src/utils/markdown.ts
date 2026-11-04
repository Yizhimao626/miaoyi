import { escapeHtml } from './storage'

/**
 * 轻量安全的 Markdown 渲染器（无外部依赖）
 *
 * 用途：消息气泡展示。修复 GLM-4 返回的 markdown（**加粗**、```代码块```）
 * 以原始符号显示、被用户视为"出现代码"的问题。
 *
 * 安全流程：抽取代码段 → 转义全部 HTML → 块级/行内转换 → 还原代码段。
 * 因先转义，任何 <script> 等危险标签都已变成实体；URL 仅允许 http/https/相对路径。
 */

// 用 \u0000 包裹的占位符保护代码段，避免被行内规则误伤
const blockPlaceholder = (i: number) => `\u0000BC${i}\u0000`
const inlinePlaceholder = (i: number) => `\u0000IC${i}\u0000`

/** URL 协议白名单：仅允许 http/https/相对路径，拒绝 javascript:/data:/vbscript 等 */
function isSafeUrl(url: string): boolean {
  return /^(https?:\/\/|\/)/i.test(url)
}

/** 属性值安全化：转义引号（escapeHtml 不转义引号） */
function attr(url: string): string {
  return url.replace(/"/g, '&quot;')
}

export function renderMarkdown(text: string): string {
  if (!text) return ''

  const blockCodes: string[] = []
  const inlineCodes: string[] = []

  // 1. 抽取 ``` 代码块（含可选语言标识）
  let work = text.replace(/```(\w*)\n?([\s\S]*?)```/g, (_m, _lang, code) => {
    const i = blockCodes.length
    blockCodes.push(`<pre class="md-code-block"><code>${escapeHtml(code.replace(/\n$/, ''))}</code></pre>`)
    return blockPlaceholder(i)
  })

  // 2. 抽取 `行内代码`
  work = work.replace(/`([^`\n]+)`/g, (_m, code) => {
    const i = inlineCodes.length
    inlineCodes.push(`<code class="md-inline">${escapeHtml(code)}</code>`)
    return inlinePlaceholder(i)
  })

  // 3. 转义剩余文本的 HTML（危险标签此时全部变成实体）
  work = escapeHtml(work)

  // 4. 块级处理（逐行）
  const lines = work.split('\n')
  const out: string[] = []
  let inList = false
  const closeList = () => {
    if (inList) {
      out.push('</ul>')
      inList = false
    }
  }
  for (const line of lines) {
    // 占位符行原样保留，不参与块级规则
    if (/^\u0000BC\d+\u0000$/.test(line.trim())) {
      closeList()
      out.push(line)
      continue
    }
    // 标题 # / ## / ###
    const h = line.match(/^(#{1,3})\s+(.*)$/)
    if (h) {
      closeList()
      const level = h[1].length
      out.push(`<h${level} class="md-h${level}">${h[2]}</h${level}>`)
      continue
    }
    // 引用 > （转义后 > 变成 &gt;）
    if (/^&gt;\s?/.test(line)) {
      closeList()
      out.push(`<blockquote class="md-quote">${line.replace(/^&gt;\s?/, '')}</blockquote>`)
      continue
    }
    // 无序列表 - * +
    if (/^[-*+]\s+/.test(line)) {
      if (!inList) {
        out.push('<ul class="md-list">')
        inList = true
      }
      out.push(`<li>${line.replace(/^[-*+]\s+/, '')}</li>`)
      continue
    }
    // 有序列表 1.
    const ol = line.match(/^\d+\.\s+(.*)$/)
    if (ol) {
      if (!inList) {
        out.push('<ul class="md-list">')
        inList = true
      }
      out.push(`<li>${ol[1]}</li>`)
      continue
    }
    // 普通行
    closeList()
    out.push(line)
  }
  closeList()
  work = out.join('\n')

  // 5. 行内转换
  // 图片 ![alt](url)
  work = work.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (_m, alt, url) => {
    if (!isSafeUrl(url)) return alt
    return `<img src="${attr(url)}" alt="${alt}" class="md-img" />`
  })
  // 链接 [text](url)
  work = work.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_m, label, url) => {
    if (!isSafeUrl(url)) return label
    return `<a href="${attr(url)}" target="_blank" rel="noopener noreferrer">${label}</a>`
  })
  // 粗体 **text** / __text__
  work = work.replace(/\*\*([^\*]+)\*\*/g, '<strong>$1</strong>')
  work = work.replace(/__([^_]+)__/g, '<strong>$1</strong>')
  // 斜体 *text* / _text_（需前后非同类标记，避免与粗体冲突）
  work = work.replace(/(^|[^\*])\*([^\*\n]+)\*(?!\*)/g, '$1<em>$2</em>')
  work = work.replace(/(^|[^_])_([^_\n]+)_(?!_)/g, '$1<em>$2</em>')
  // 删除线 ~~text~~
  work = work.replace(/~~([^~]+)~~/g, '<del>$1</del>')
  // 换行
  work = work.replace(/\n/g, '<br>')

  // 6. 还原代码占位符
  work = work.replace(/\u0000BC(\d+)\u0000/g, (_m, i) => blockCodes[Number(i)] || '')
  work = work.replace(/\u0000IC(\d+)\u0000/g, (_m, i) => inlineCodes[Number(i)] || '')

  return work
}
