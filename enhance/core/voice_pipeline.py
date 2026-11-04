"""
喵艺 · 全双工语音管线
两条路线（择一即可）：
  A. 后端 ASR（本文件 VoicePipeline）：接收 PCM 音频流，用 funasr/whisper 转写
  B. 前端 ASR（浏览器 Web Speech API）：前端识别出文字后通过 WS 发 voice_text
     → 直接走 call_manager.bot_reply_to_voice，零后端依赖，推荐先用这条
"""
from __future__ import annotations

import asyncio
import io
import time
from typing import Optional, Protocol


class ASRClient(Protocol):
    async def transcribe(self, audio: bytes, sample_rate: int = 16000) -> str: ...


class VAD:
    """极简能量门限 VAD：按分贝判断是否在说话，静音超过阈值则认为一句话结束。"""

    def __init__(self, silence_threshold_db: float = -40.0, silence_pad_s: float = 0.6):
        self.silence_threshold_db = silence_threshold_db
        self.silence_pad_s = silence_pad_s
        self._speaking = False
        self._buffer = bytearray()
        self._last_voice_ts = 0.0

    def feed(self, pcm: bytes) -> bytes | None:
        """喂入一段 PCM(16bit mono)。返回一个完整句子的 PCM 或 None。"""
        import math
        n = len(pcm) // 2
        if n == 0:
            return None
        sq = 0
        for i in range(0, len(pcm), 2):
            v = int.from_bytes(pcm[i:i + 2], "little", signed=True)
            sq += v * v
        rms = math.sqrt(sq / n)
        db = 20 * math.log10(rms + 1e-9)
        voice = db > self.silence_threshold_db

        now = time.time()
        if voice:
            self._speaking = True
            self._buffer.extend(pcm)
            self._last_voice_ts = now
            return None

        if self._speaking:
            self._buffer.extend(pcm)
            if now - self._last_voice_ts >= self.silence_pad_s:
                utterance = bytes(self._buffer)
                self._buffer.clear()
                self._speaking = False
                return utterance if len(utterance) > 3200 else None
        return None


class VoicePipeline:
    def __init__(self, asr: ASRClient, on_utterance):
        self.asr = asr
        self.on_utterance = on_utterance
        self.vad = VAD()
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._task: asyncio.Task | None = None

    async def start(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self):
        await self._queue.put(b"")
        if self._task:
            await self._task

    def push_audio(self, pcm: bytes):
        self._queue.put_nowait(pcm)

    async def _run(self):
        while True:
            chunk = await self._queue.get()
            if not chunk:
                break
            utterance = self.vad.feed(chunk)
            if utterance:
                try:
                    text = await self.asr.transcribe(utterance)
                    text = (text or "").strip()
                    if text:
                        await self.on_utterance(text)
                except Exception as e:
                    print(f"[voice_pipeline] asr error: {e}")


# ---------- ASR 实现示例（二选一，暂不启用，先用前端浏览器 ASR） ----------
class FunASRClient:
    """基于阿里 funasr 的本地 ASR。pip install funasr"""
    def __init__(self, model="paraformer-zh"):
        self._model = None
        self._name = model

    async def transcribe(self, audio: bytes, sample_rate: int = 16000) -> str:
        if self._model is None:
            import funasr
            self._model = funasr.AutoModel(model=self._name)
        loop = asyncio.get_event_loop()
        res = await loop.run_in_executor(
            None, lambda: self._model.generate(input=io.BytesIO(audio), batch_size_s=300)
        )
        if res and "text" in res[0]:
            return res[0]["text"]
        return ""


class WhisperClient:
    """基于 faster-whisper。pip install faster-whisper"""
    def __init__(self, model="small", device="cpu"):
        self._model = None
        self._name = model
        self._device = device

    async def transcribe(self, audio: bytes, sample_rate: int = 16000) -> str:
        if self._model is None:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(self._name, device=self._device, compute_type="int8")
        import numpy as np
        arr = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
        loop = asyncio.get_event_loop()
        segs, _ = await loop.run_in_executor(
            None, lambda: self._model.transcribe(arr, language="zh")
        )
        return "".join(s.text for s in segs).strip()
