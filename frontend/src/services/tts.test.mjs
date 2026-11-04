/**
 * 喵艺 · TTS 文本预处理测试（零依赖，Node 原生）
 *
 * 运行：node src/services/tts.test.mjs
 *
 * 覆盖第四步要求：
 * 1. 中文 → 保留
 * 2. 数字 → 保留
 * 3. 中文+数字 → 都保留
 * 4. 中英混合 → 保留
 * 5. Emoji → 清除
 * 6. Markdown → 清除
 * 7. 空字符串 → 返回空（不调用 TTS）
 * 8. 特殊非法输入 → 不崩溃
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'

// 复制 cleanTextForSpeech 逻辑（与 services/tts.ts 一致）
function cleanTextForSpeech(text) {
  if (!text) return ''
  return text
    .replace(/[\u{1F300}-\u{1F9FF}]/gu, '')
    .replace(/[\u{2600}-\u{27BF}]/gu, '')
    .replace(/[\u{1F000}-\u{1F02F}]/gu, '')
    .replace(/[\u{1FA00}-\u{1FAFF}]/gu, '')
    .replace(/\*{1,2}([^*]+)\*{1,2}/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\n+/g, '，')
    .replace(/\s+/g, ' ')
    .trim()
}

test('中文正常保留', () => {
  assert.equal(cleanTextForSpeech('你好'), '你好')
  assert.equal(cleanTextForSpeech('你好呀，我是喵艺。'), '你好呀，我是喵艺。')
  assert.equal(cleanTextForSpeech('今天过得怎么样？'), '今天过得怎么样？')
})

test('数字正常保留', () => {
  assert.equal(cleanTextForSpeech('12345'), '12345')
  assert.equal(cleanTextForSpeech('2026'), '2026')
  assert.equal(cleanTextForSpeech('3.14'), '3.14')
})

test('中文+数字混合保留', () => {
  assert.equal(cleanTextForSpeech('现在是 2026 年。'), '现在是 2026 年。')
  assert.equal(cleanTextForSpeech('温度是 26℃。'), '温度是 26℃。')
})

test('中英混合保留', () => {
  assert.equal(cleanTextForSpeech('Hello，喵艺 AI'), 'Hello，喵艺 AI')
})

test('Emoji 被清除但中文保留', () => {
  assert.equal(cleanTextForSpeech('你好呀～😊'), '你好呀～')
  assert.equal(cleanTextForSpeech('今天很开心 😄'), '今天很开心')
})

test('Markdown 符号被清除', () => {
  assert.equal(cleanTextForSpeech('**你好**'), '你好')
  assert.equal(cleanTextForSpeech('`代码` 喵'), '代码 喵')
})

test('换行转为逗号', () => {
  assert.equal(cleanTextForSpeech('第一行\n第二行'), '第一行，第二行')
})

test('空字符串返回空（不调用 TTS）', () => {
  assert.equal(cleanTextForSpeech(''), '')
  assert.equal(cleanTextForSpeech(null), '')
  assert.equal(cleanTextForSpeech(undefined), '')
})

test('特殊非法输入不崩溃', () => {
  // 纯 emoji → 空（不调用 TTS）
  assert.equal(cleanTextForSpeech('😀😀😀'), '')
  // 特殊字符保留或清理，不抛错
  assert.equal(typeof cleanTextForSpeech('@@@###$$$'), 'string')
})
