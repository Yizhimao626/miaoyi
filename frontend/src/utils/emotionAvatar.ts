/**
 * 喵艺 · 情绪 → 头像 统一映射
 * ------------------------------------------------------------
 * 素材说明：官方四情绪头像（开心/平静/难过/生气）已于 2026-08 从
 * ui图片/版图.png（官方设计版图）提取入库，负面情绪不再"笑着代替"。
 * 旧素材 smile/normal 等保留，供贴纸与兜底使用。
 *
 * 映射策略：
 * 1. 精确匹配已知情绪 → 对应头像
 * 2. 未匹配/未知/null/undefined/空串 → 回退 normal（绝不报错）
 */
import type { EmotionalState } from '../types'

export const AVATAR_BASE = 'miaoyi/avatars'

/** 情绪名 → 头像文件名（官方四情绪 + 兜底） */
const EMOTION_AVATAR_MAP: Record<string, string> = {
  // 开心系（版图官方 happy）
  smile: 'happy.png',
  smiling: 'happy.png',
  happy: 'happy.png',
  joy: 'happy.png',
  excited: 'happy.png',
  excitement: 'happy.png',
  // 平静系（版图官方 calm）
  content: 'calm.png',
  contentment: 'calm.png',
  calm: 'calm.png',
  serene: 'calm.png',
  serenity: 'calm.png',
  shy: 'calm.png',
  shyness: 'calm.png',
  // 中性/兜底
  neutral: 'normal.png',
  normal: 'normal.png',
  quiet: 'normal.png',
  bored: 'normal.png',
  boredom: 'normal.png',
  tired: 'normal.png',
  fatigue: 'normal.png',
  sleepy: 'normal.png',
  // 难过系（版图官方 sad，2026-08 补齐，不再用笑脸代替）
  sad: 'sad.png',
  sadness: 'sad.png',
  lonely: 'sad.png',
  loneliness: 'sad.png',
  // 生气系（版图官方 angry，2026-08 补齐）
  angry: 'angry.png',
  anger: 'angry.png',
}

/** 从情绪名解析头像路径（永不抛错，未知一律回退 normal） */
export function getEmotionAvatarPath(emotion?: string | null): string {
  const key = String(emotion || '').trim().toLowerCase()
  const file = EMOTION_AVATAR_MAP[key] || 'normal.png'
  return `${AVATAR_BASE}/${file}`
}

/** 从 EmotionalState 提取当前主导情绪并解析头像路径 */
export function getAvatarFromEmotionalState(state?: EmotionalState | null): string {
  if (!state) return `${AVATAR_BASE}/normal.png`
  // 兼容多种字段名（dominant_emotion / dominant / dominant_label）
  const emotion =
    state.dominant_emotion ||
    state.dominant ||
    state.dominant_label ||
    ''
  return getEmotionAvatarPath(emotion)
}
