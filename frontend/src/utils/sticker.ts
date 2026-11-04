/**
 * 喵艺 · 官方表情包（内容2 原画版 v4）
 * ------------------------------------------------------------
 * 贴纸素材统一来自官方原画表 ui图片/内容2.png（原生分辨率约为版图表的 2 倍）：
 * 10 格官方贴纸 = 8 个单人/单猫 Q 版 + 2 个整幅场景
 * （v3 曾取自版图贴纸区，源分辨率仅 60px/格导致发糊；v4 换原画源。
 * logo/wordmark 仍取自版图（品牌元素，内容2 无对应）。
 * 切割入库于 public/miaoyi/official/stickers/stk_01..10.png（3x 放大+提锐）。
 * 贴纸消息以文本标记 [sticker:xxx] 走现有发送管道，
 * 前端渲染时识别并显示为大图贴纸，后端收到的是普通文本，零兼容风险。
 * 旧版贴纸 id（含 v1 的 stk_11/12 从未发布即废弃，无需兼容）保留解析兼容：
 * 历史消息 [sticker:smile] 等仍可渲染。
 */

export interface MiaoyiSticker {
  id: string
  label: string
}

/** 官方 10 格贴纸（面板展示顺序 = 左列自上而下、右列自上而下、场景殿后） */
export const MIAOYI_STICKERS: MiaoyiSticker[] = [
  { id: 'stk_01', label: '耶!' },
  { id: 'stk_02', label: '抱猫' },
  { id: 'stk_03', label: '蹭蹭' },
  { id: 'stk_04', label: '喵~' },
  { id: 'stk_05', label: '呆猫' },
  { id: 'stk_06', label: '打滚' },
  { id: 'stk_07', label: '躺平' },
  { id: 'stk_08', label: '趴趴' },
  { id: 'stk_09', label: '贴贴' },
  { id: 'stk_10', label: '抱抱' },
]

/** 旧版贴纸 id：仅用于历史消息解析与渲染，不再进面板 */
const LEGACY_STICKER_IDS = [
  'smile', 'happy', 'calm', 'normal', 'smile_alt', 'happy_alt', 'calm_alt', 'smile_alt2',
  'chibi_06', 'chibi_07', 'chibi_08', 'chibi_09', 'chibi_12', 'chibi_13', 'chibi_14', 'chibi_15', 'chibi_23',
]

const STICKER_IDS = new Set([...MIAOYI_STICKERS.map((s) => s.id), ...LEGACY_STICKER_IDS])

const STICKER_RE = /^\[sticker:([a-z0-9_]+)\]$/i

/** 生成贴纸消息文本 */
export function buildStickerText(id: string): string {
  return `[sticker:${id}]`
}

/** 从消息文本解析贴纸 id；不是贴纸或 id 不在白名单时返回 null（防路径注入） */
export function parseSticker(text?: string | null): string | null {
  const m = String(text || '').trim().match(STICKER_RE)
  if (!m) return null
  const id = m[1].toLowerCase()
  return STICKER_IDS.has(id) ? id : null
}

/** 贴纸图片路径：官方 stk_ 走 official/stickers，旧版 chibi_/头像走旧目录 */
export function stickerImgSrc(id: string): string {
  if (id.startsWith('stk_')) return `miaoyi/official/stickers/${id}.png`
  return id.startsWith('chibi_') ? `miaoyi/chibi/${id}.png` : `miaoyi/avatars/${id}.png`
}

const STICKER_INLINE_RE = /\[sticker:([a-z0-9_]+)\]/gi

/**
 * 从任意文本中提取贴纸标记（AI 回复可能"文字 + 贴纸"混排）。
 * 返回去掉标记的干净文本与贴纸 id 列表（最多取 2 个，防刷屏）。
 * 非白名单 id 原样保留，不误伤普通文本。
 */
export function extractStickers(text: string): { cleanText: string; stickers: string[] } {
  const stickers: string[] = []
  const cleanText = String(text || '')
    .replace(STICKER_INLINE_RE, (raw, id: string) => {
      const key = id.toLowerCase()
      if (STICKER_IDS.has(key) && stickers.length < 2) {
        stickers.push(key)
        return ''
      }
      return raw
    })
    .replace(/[ \t]+\n/g, '\n')
    .trim()
  return { cleanText, stickers }
}
