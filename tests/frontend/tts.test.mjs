import { test } from 'node:test'
import assert from 'node:assert/strict'
import { cleanTextForSpeech, resolveAudioUrl } from './.build/tts.mjs'

test('cleanTextForSpeech 保留中文/数字/英文', () => {
  assert.equal(cleanTextForSpeech('我有3个苹果和2个梨'), '我有3个苹果和2个梨')
  assert.equal(cleanTextForSpeech('Hello World 123'), 'Hello World 123')
})

test('cleanTextForSpeech 去除 emoji', () => {
  assert.equal(cleanTextForSpeech('我好开心😄🎉呀'), '我好开心呀')
})

test('cleanTextForSpeech 贴纸标记不朗读', () => {
  assert.equal(cleanTextForSpeech('[sticker:smile]你好'), '你好')
})

test('cleanTextForSpeech markdown 去符号留内容', () => {
  assert.equal(cleanTextForSpeech('**重点**和`代码`'), '重点和代码')
})

test('cleanTextForSpeech 换行转逗号', () => {
  assert.equal(cleanTextForSpeech('第一行\n第二行'), '第一行，第二行')
})

test('cleanTextForSpeech 空输入', () => {
  assert.equal(cleanTextForSpeech(''), '')
})

test('resolveAudioUrl 相对路径补全后端地址', () => {
  assert.equal(resolveAudioUrl('/audio/a.wav'), 'http://127.0.0.1:8001/audio/a.wav')
  assert.equal(resolveAudioUrl('audio/a.wav'), 'http://127.0.0.1:8001/audio/a.wav')
})

test('resolveAudioUrl 绝对/blob/data 地址原样返回', () => {
  assert.equal(resolveAudioUrl('http://x/a.wav'), 'http://x/a.wav')
  assert.equal(resolveAudioUrl('https://x/a.wav'), 'https://x/a.wav')
  assert.equal(resolveAudioUrl('blob:abc'), 'blob:abc')
  assert.equal(resolveAudioUrl('data:audio/wav;base64,xx'), 'data:audio/wav;base64,xx')
  assert.equal(resolveAudioUrl(''), '')
})
