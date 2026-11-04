// src/utils/sticker.ts
var MIAOYI_STICKERS = [
  { id: "stk_01", label: "\u8036!" },
  { id: "stk_02", label: "\u62B1\u732B" },
  { id: "stk_03", label: "\u8E6D\u8E6D" },
  { id: "stk_04", label: "\u55B5~" },
  { id: "stk_05", label: "\u5446\u732B" },
  { id: "stk_06", label: "\u6253\u6EDA" },
  { id: "stk_07", label: "\u8EBA\u5E73" },
  { id: "stk_08", label: "\u8DB4\u8DB4" },
  { id: "stk_09", label: "\u8D34\u8D34" },
  { id: "stk_10", label: "\u62B1\u62B1" }
];
var LEGACY_STICKER_IDS = [
  "smile",
  "happy",
  "calm",
  "normal",
  "smile_alt",
  "happy_alt",
  "calm_alt",
  "smile_alt2",
  "chibi_06",
  "chibi_07",
  "chibi_08",
  "chibi_09",
  "chibi_12",
  "chibi_13",
  "chibi_14",
  "chibi_15",
  "chibi_23"
];
var STICKER_IDS = /* @__PURE__ */ new Set([...MIAOYI_STICKERS.map((s) => s.id), ...LEGACY_STICKER_IDS]);
var STICKER_RE = /^\[sticker:([a-z0-9_]+)\]$/i;
function buildStickerText(id) {
  return `[sticker:${id}]`;
}
function parseSticker(text) {
  const m = String(text || "").trim().match(STICKER_RE);
  if (!m) return null;
  const id = m[1].toLowerCase();
  return STICKER_IDS.has(id) ? id : null;
}
function stickerImgSrc(id) {
  if (id.startsWith("stk_")) return `miaoyi/official/stickers/${id}.png`;
  return id.startsWith("chibi_") ? `miaoyi/chibi/${id}.png` : `miaoyi/avatars/${id}.png`;
}
var STICKER_INLINE_RE = /\[sticker:([a-z0-9_]+)\]/gi;
function extractStickers(text) {
  const stickers = [];
  const cleanText = String(text || "").replace(STICKER_INLINE_RE, (raw, id) => {
    const key = id.toLowerCase();
    if (STICKER_IDS.has(key) && stickers.length < 2) {
      stickers.push(key);
      return "";
    }
    return raw;
  }).replace(/[ \t]+\n/g, "\n").trim();
  return { cleanText, stickers };
}
export {
  MIAOYI_STICKERS,
  buildStickerText,
  extractStickers,
  parseSticker,
  stickerImgSrc
};
