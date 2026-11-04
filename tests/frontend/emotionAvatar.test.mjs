import { test } from 'node:test'
import assert from 'node:assert/strict'
import { AVATAR_BASE, getEmotionAvatarPath, getAvatarFromEmotionalState } from './.build/emotionAvatar.mjs'

test('已知情绪精确映射', () => {
  assert.equal(getEmotionAvatarPath('happy'), `${AVATAR_BASE}/happy.png`)
  // 2026-08：smile 归入官方 happy 头像
  assert.equal(getEmotionAvatarPath('smile'), `${AVATAR_BASE}/happy.png`)
  assert.equal(getEmotionAvatarPath('content'), `${AVATAR_BASE}/calm.png`)
  assert.equal(getEmotionAvatarPath('neutral'), `${AVATAR_BASE}/normal.png`)
})

test('大小写与空白容错', () => {
  assert.equal(getEmotionAvatarPath(' HAPPY '), `${AVATAR_BASE}/happy.png`)
})

test('负面情绪用版图官方头像（2026-08 补齐，不再近似代替）', () => {
  assert.equal(getEmotionAvatarPath('sad'), `${AVATAR_BASE}/sad.png`)
  assert.equal(getEmotionAvatarPath('angry'), `${AVATAR_BASE}/angry.png`)
  assert.equal(getEmotionAvatarPath('bored'), `${AVATAR_BASE}/normal.png`)
})

test('未知/空一律回退 normal，永不抛错', () => {
  assert.equal(getEmotionAvatarPath('not_an_emotion'), `${AVATAR_BASE}/normal.png`)
  assert.equal(getEmotionAvatarPath(''), `${AVATAR_BASE}/normal.png`)
  assert.equal(getEmotionAvatarPath(null), `${AVATAR_BASE}/normal.png`)
  assert.equal(getEmotionAvatarPath(undefined), `${AVATAR_BASE}/normal.png`)
})

test('getAvatarFromEmotionalState 多字段名兼容', () => {
  assert.equal(getAvatarFromEmotionalState({ dominant_emotion: 'joy' }), `${AVATAR_BASE}/happy.png`)
  assert.equal(getAvatarFromEmotionalState({ dominant: 'calm' }), `${AVATAR_BASE}/calm.png`)
  assert.equal(getAvatarFromEmotionalState({ dominant_label: 'excited' }), `${AVATAR_BASE}/happy.png`)
  assert.equal(getAvatarFromEmotionalState(null), `${AVATAR_BASE}/normal.png`)
  assert.equal(getAvatarFromEmotionalState({}), `${AVATAR_BASE}/normal.png`)
})
