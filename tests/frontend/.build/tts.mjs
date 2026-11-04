// ../tests/frontend/stubs/api.mjs
async function synthesizeVoice() {
  throw new Error("stub: synthesizeVoice \u4E0D\u5E94\u5728\u7EAF\u51FD\u6570\u6D4B\u8BD5\u4E2D\u88AB\u8C03\u7528");
}

// ../tests/frontend/stubs/config.mjs
function getBaseUrl() {
  return "http://127.0.0.1:8001";
}

// ../tests/frontend/stubs/storage.mjs
function getMute() {
  return false;
}

// src/services/tts.ts
var _audio = null;
var _objectUrl = null;
var _listeners = /* @__PURE__ */ new Set();
function notify(state, error) {
  _listeners.forEach((fn) => fn(state, error));
}
function onTtsState(fn) {
  _listeners.add(fn);
  return () => _listeners.delete(fn);
}
function releaseObjectUrl() {
  if (_objectUrl) {
    try {
      URL.revokeObjectURL(_objectUrl);
    } catch {
    }
    _objectUrl = null;
  }
}
function cleanupAudio() {
  if (_audio) {
    try {
      _audio.onended = null;
      _audio.onerror = null;
      _audio.pause();
    } catch {
    }
    _audio = null;
  }
  releaseObjectUrl();
}
function cleanTextForSpeech(text) {
  if (!text) return "";
  return text.replace(/\[sticker:[a-z0-9_]+\]/gi, "").replace(/[\u{1F300}-\u{1F9FF}]/gu, "").replace(/[\u{2600}-\u{27BF}]/gu, "").replace(/[\u{1F000}-\u{1F02F}]/gu, "").replace(/[\u{1FA00}-\u{1FAFF}]/gu, "").replace(/\*{1,2}([^*]+)\*{1,2}/g, "$1").replace(/`([^`]+)`/g, "$1").replace(/\n+/g, "\uFF0C").replace(/\s+/g, " ").trim();
}
function resolveAudioUrl(url) {
  if (!url) return "";
  if (/^(https?:|blob:|data:)/.test(url)) return url;
  const base = getBaseUrl();
  if (base.startsWith("http")) {
    return `${base}${url.startsWith("/") ? "" : "/"}${url}`;
  }
  return url.startsWith("/") ? url : `/${url}`;
}
function stopTts() {
  cleanupAudio();
  notify("idle");
}
function isTtsPlaying() {
  return !!_audio && !_audio.paused;
}
async function textToSpeech(text) {
  const clean = cleanTextForSpeech(text);
  if (!clean) {
    notify("error", "\u6587\u672C\u4E3A\u7A7A\uFF0C\u4E0D\u8C03\u7528 TTS");
    return false;
  }
  if (getMute()) {
    notify("idle");
    return false;
  }
  cleanupAudio();
  notify("loading");
  try {
    const result = await synthesizeVoice(clean);
    const audioUrl = result?.data?.audio_url;
    if (!audioUrl) {
      notify("error", "\u540E\u7AEF\u672A\u8FD4\u56DE\u97F3\u9891\u5730\u5740");
      console.error("[TTS] \u540E\u7AEF\u672A\u8FD4\u56DE audio_url, text=", clean.slice(0, 30));
      return false;
    }
    const fullUrl = resolveAudioUrl(audioUrl);
    const audio = new Audio(fullUrl);
    _audio = audio;
    return await new Promise((resolve) => {
      let settled = false;
      const finish = (ok, err) => {
        if (settled) return;
        settled = true;
        if (ok) notify("playing");
        else notify("error", err);
        resolve(ok);
      };
      audio.onplay = () => finish(true);
      audio.onended = () => {
        notify("idle");
        cleanupAudio();
      };
      audio.onpause = () => {
        if (audio.paused && audio.currentTime === 0) {
        }
      };
      audio.onerror = () => {
        console.error("[TTS] \u97F3\u9891\u64AD\u653E\u5931\u8D25", fullUrl.slice(0, 80));
        finish(false, "\u97F3\u9891\u64AD\u653E\u5931\u8D25");
        cleanupAudio();
      };
      audio.play().catch((e) => {
        console.error("[TTS] play() \u88AB\u62D2\u7EDD\uFF08\u53EF\u80FD autoplay \u9650\u5236\uFF09:", e);
        finish(false, "\u6D4F\u89C8\u5668\u963B\u6B62\u4E86\u81EA\u52A8\u64AD\u653E\uFF0C\u8BF7\u70B9\u51FB\u9875\u9762\u540E\u518D\u8BD5");
        cleanupAudio();
      });
    });
  } catch (e) {
    console.error("[TTS] \u5408\u6210\u8BF7\u6C42\u5931\u8D25:", e);
    notify("error", "\u8BED\u97F3\u5408\u6210\u5931\u8D25");
    return false;
  }
}
function disposeTts() {
  cleanupAudio();
  _listeners.clear();
}
export {
  cleanTextForSpeech,
  disposeTts,
  isTtsPlaying,
  onTtsState,
  resolveAudioUrl,
  stopTts,
  textToSpeech
};
