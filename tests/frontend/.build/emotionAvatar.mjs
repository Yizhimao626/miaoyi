// src/utils/emotionAvatar.ts
var AVATAR_BASE = "miaoyi/avatars";
var EMOTION_AVATAR_MAP = {
  // 开心系（版图官方 happy）
  smile: "happy.png",
  smiling: "happy.png",
  happy: "happy.png",
  joy: "happy.png",
  excited: "happy.png",
  excitement: "happy.png",
  // 平静系（版图官方 calm）
  content: "calm.png",
  contentment: "calm.png",
  calm: "calm.png",
  serene: "calm.png",
  serenity: "calm.png",
  shy: "calm.png",
  shyness: "calm.png",
  // 中性/兜底
  neutral: "normal.png",
  normal: "normal.png",
  quiet: "normal.png",
  bored: "normal.png",
  boredom: "normal.png",
  tired: "normal.png",
  fatigue: "normal.png",
  sleepy: "normal.png",
  // 难过系（版图官方 sad，2026-08 补齐，不再用笑脸代替）
  sad: "sad.png",
  sadness: "sad.png",
  lonely: "sad.png",
  loneliness: "sad.png",
  // 生气系（版图官方 angry，2026-08 补齐）
  angry: "angry.png",
  anger: "angry.png"
};
function getEmotionAvatarPath(emotion) {
  const key = String(emotion || "").trim().toLowerCase();
  const file = EMOTION_AVATAR_MAP[key] || "normal.png";
  return `${AVATAR_BASE}/${file}`;
}
function getAvatarFromEmotionalState(state) {
  if (!state) return `${AVATAR_BASE}/normal.png`;
  const emotion = state.dominant_emotion || state.dominant || state.dominant_label || "";
  return getEmotionAvatarPath(emotion);
}
export {
  AVATAR_BASE,
  getAvatarFromEmotionalState,
  getEmotionAvatarPath
};
