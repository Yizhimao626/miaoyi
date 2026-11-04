import { test } from 'node:test'
import assert from 'node:assert/strict'
import { formatTime, searchMessages } from './.build/messageGroup.mjs'

test('formatTime 本地时:分 补零', () => {
  assert.equal(formatTime(new Date(2026, 7, 20, 9, 5).getTime()), '09:05')
  assert.equal(formatTime(new Date(2026, 7, 20, 23, 59).getTime()), '23:59')
})

test('searchMessages 文本/思考内容搜索，大小写不敏感', () => {
  const msgs = [
    { id: '1', text: '你好喵', role: 'user' },
    { id: '2', text: '今天天气不错', role: 'ai', thought: '想出去玩' },
    { id: '3', text: 'HELLO world', role: 'user' },
  ]
  assert.equal(searchMessages(msgs, 'hello').length, 1)
  assert.equal(searchMessages(msgs, '你好').length, 1)
  assert.equal(searchMessages(msgs, '想出去玩').length, 1)
  assert.equal(searchMessages(msgs, '   ').length, 0)
})
