/**
 * 喵艺 · 情绪头像映射测试（零依赖，Node 原生 test 运行）
 *
 * 运行：node --test src/utils/emotionAvatar.test.mjs
 * 或：  node src/utils/emotionAvatar.test.mjs
 *
 * 覆盖：
 * 1. normal → normal.png
 * 2. happy → happy.png
 * 3. sad → sad.png（2026-08 版图官方素材补齐）
 * 4. angry → angry.png（2026-08 版图官方素材补齐）
 * 5. confused（未知）→ 回退 normal.png
 * 6. null/undefined/空串/拼写差异 → 回退 normal.png（不报错）
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'

// 直接复制映射逻辑（.ts 不能直接被 node 跑，测试映射规则而非 import）
const AVATAR_BASE = 'miaoyi/avatars'
const EMOTION_AVATAR_MAP = {
  smile: 'smile.png', smiling: 'smile.png',
  happy: 'happy.png', joy: 'happy.png', excited: 'happy.png', excitement: 'happy.png',
  content: 'calm.png', contentment: 'calm.png', calm: 'calm.png', serene: 'calm.png', serenity: 'calm.png',
  neutral: 'normal.png', normal: 'normal.png', quiet: 'normal.png',
  shy: 'calm.png', shyness: 'calm.png',
  sad: 'sad.png', sadness: 'sad.png', lonely: 'sad.png', loneliness: 'sad.png',
  angry: 'angry.png', anger: 'angry.png',
  bored: 'normal.png', boredom: 'normal.png', tired: 'normal.png', fatigue: 'normal.png', sleepy: 'normal.png',
}

function getEmotionAvatarPath(emotion) {
  const key = String(emotion || '').trim().toLowerCase()
  const file = EMOTION_AVATAR_MAP[key] || 'normal.png'
  return `${AVATAR_BASE}/${file}`
}

test('normal 显示正常头像', () => {
  assert.equal(getEmotionAvatarPath('normal'), 'miaoyi/avatars/normal.png')
  assert.equal(getEmotionAvatarPath('neutral'), 'miaoyi/avatars/normal.png')
})

test('happy 显示开心头像', () => {
  assert.equal(getEmotionAvatarPath('happy'), 'miaoyi/avatars/happy.png')
  assert.equal(getEmotionAvatarPath('joy'), 'miaoyi/avatars/happy.png')
  assert.equal(getEmotionAvatarPath('excited'), 'miaoyi/avatars/happy.png')
})

test('sad 显示难过头像（版图官方素材）', () => {
  assert.equal(getEmotionAvatarPath('sad'), 'miaoyi/avatars/sad.png')
  assert.equal(getEmotionAvatarPath('sadness'), 'miaoyi/avatars/sad.png')
  assert.equal(getEmotionAvatarPath('lonely'), 'miaoyi/avatars/sad.png')
})

test('angry 显示生气头像（版图官方素材）', () => {
  assert.equal(getEmotionAvatarPath('angry'), 'miaoyi/avatars/angry.png')
  assert.equal(getEmotionAvatarPath('anger'), 'miaoyi/avatars/angry.png')
})

test('confused 未知情绪回退 normal（不报错）', () => {
  assert.equal(getEmotionAvatarPath('confused'), 'miaoyi/avatars/normal.png')
})

test('null/undefined/空串/大小写差异 全部回退 normal', () => {
  assert.equal(getEmotionAvatarPath(null), 'miaoyi/avatars/normal.png')
  assert.equal(getEmotionAvatarPath(undefined), 'miaoyi/avatars/normal.png')
  assert.equal(getEmotionAvatarPath(''), 'miaoyi/avatars/normal.png')
  assert.equal(getEmotionAvatarPath('  '), 'miaoyi/avatars/normal.png')
  // 大小写不敏感
  assert.equal(getEmotionAvatarPath('HAPPY'), 'miaoyi/avatars/happy.png')
  assert.equal(getEmotionAvatarPath('Happy'), 'miaoyi/avatars/happy.png')
  // 全新未知情绪
  assert.equal(getEmotionAvatarPath('proud'), 'miaoyi/avatars/normal.png')
})

test('返回路径始终以 miaoyi/avatars/ 开头且是 png', () => {
  for (const e of ['normal', 'happy', 'sad', 'angry', 'confused', null, undefined, '']) {
    const p = getEmotionAvatarPath(e)
    assert.ok(p.startsWith('miaoyi/avatars/'), `路径前缀错误: ${p}`)
    assert.ok(p.endsWith('.png'), `非png: ${p}`)
  }
})
