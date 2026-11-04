import { test } from 'node:test'
import assert from 'node:assert/strict'
import { MIAOYI_STICKERS, buildStickerText, parseSticker, stickerImgSrc, extractStickers } from './.build/sticker.mjs'

test('buildStickerText 生成标记', () => {
  assert.equal(buildStickerText('smile'), '[sticker:smile]')
})

test('parseSticker 白名单内全量往返', () => {
  for (const s of MIAOYI_STICKERS) {
    assert.equal(parseSticker(buildStickerText(s.id)), s.id)
  }
})

test('parseSticker 白名单/格式防御', () => {
  assert.equal(parseSticker('[sticker:not_exist]'), null)
  assert.equal(parseSticker('你好'), null)
  assert.equal(parseSticker(''), null)
  assert.equal(parseSticker(null), null)
  assert.equal(parseSticker('前后有文字[sticker:smile]'), null)
})

test('parseSticker 大小写与空白容错', () => {
  assert.equal(parseSticker('[sticker:SMILE]'), 'smile')
  assert.equal(parseSticker('  [sticker:happy]  '), 'happy')
})

test('stickerImgSrc 路径分流', () => {
  assert.equal(stickerImgSrc('smile'), 'miaoyi/avatars/smile.png')
  assert.equal(stickerImgSrc('chibi_06'), 'miaoyi/chibi/chibi_06.png')
})

test('extractStickers 文字+贴纸混排', () => {
  const r = extractStickers('你好[sticker:smile]世界[sticker:happy]')
  assert.equal(r.cleanText, '你好世界')
  assert.deepEqual(r.stickers, ['smile', 'happy'])
})

test('extractStickers 最多取2个防刷屏', () => {
  const r = extractStickers('[sticker:smile][sticker:happy][sticker:calm]')
  assert.equal(r.stickers.length, 2)
  assert.deepEqual(r.stickers, ['smile', 'happy'])
})

test('extractStickers 非白名单id不误伤', () => {
  const r = extractStickers('看[sticker:fake_id]喵')
  assert.equal(r.cleanText, '看[sticker:fake_id]喵')
  assert.deepEqual(r.stickers, [])
})

test('extractStickers 空文本', () => {
  assert.deepEqual(extractStickers(''), { cleanText: '', stickers: [] })
})
