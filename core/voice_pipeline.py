"""
喵艺 AI 伴侣 · 全双工语音管线
==============================
职责：
  - 前端麦克风 → WebSocket 音频流 → 后端 VAD 分句 → ASR → 回调
  - 支持后端 ASR（FunASR/Whisper）和前端 ASR（浏览器 Web Speech API）
  - VAD 语音活动检测

两条路线：
  A. 后端 ASR（VoicePipeline）：接收 PCM 音频流
  B. 前端 ASR（浏览器 Web Speech API）：前端识别文字后通过 WS 发送
"""

import logging
import struct
import time
from typing import AsyncGenerator, Callable, List, Optional, Tuple

_log = logging.getLogger("VoicePipeline")


# ============================================================
# VAD 语音活动检测
# ============================================================

class SimpleVAD:
    """
    极简能量门限 VAD

    按分贝判断是否在说话，静音超过 0.6 秒则认为一句话结束。
    过滤 3200 字节以下的噪声。
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        threshold_db: float = -30.0,
        silence_timeout: float = 0.6,
        min_chunk_bytes: int = 3200,
    ):
        """
        Args:
            sample_rate: 采样率
            threshold_db: 能量阈值 (dB)
            silence_timeout: 静音超时（秒）
            min_chunk_bytes: 最小有效块字节数
        """
        self._sample_rate = sample_rate
        self._threshold_db = threshold_db
        self._silence_timeout = silence_timeout
        self._min_chunk_bytes = min_chunk_bytes

        self._buffer = bytearray()
        self._last_voice_time: float | None = None
        self._is_speaking = False
        self._chunk_start_time: float | None = None

    def reset(self):
        """重置状态"""
        self._buffer.clear()
        self._last_voice_time = None
        self._is_speaking = False
        self._chunk_start_time = None

    def process_audio(self, audio_data: bytes) -> tuple[list[bytes], bool]:
        """
        处理音频数据，返回完整的语音片段列表

        Args:
            audio_data: PCM 16-bit 音频数据

        Returns:
            Tuple[List[bytes], bool]: (完成的语音片段, 是否仍在说话)
        """
        if len(audio_data) < 100:
            return [], self._is_speaking

        self._buffer.extend(audio_data)
        current_time = time.time()

        # 计算当前音频能量
        energy = self._calculate_energy(audio_data)
        is_voice = energy > self._threshold_db

        if is_voice:
            if not self._is_speaking:
                _log.debug(f"VAD: 检测到语音开始, energy={energy:.1f}dB")
                self._chunk_start_time = current_time
            self._is_speaking = True
            self._last_voice_time = current_time
        else:
            if self._is_speaking:
                silence_duration = (
                    current_time - self._last_voice_time
                    if self._last_voice_time else float('inf')
                )

                if silence_duration >= self._silence_timeout:
                    # 静音超时，认为一句话结束
                    chunks = self._extract_complete_chunks()
                    self._is_speaking = False
                    _log.debug(
                        f"VAD: 检测到语音结束, "
                        f"silence={silence_duration:.1f}s, "
                        f"chunks={len(chunks)}"
                    )
                    return chunks, False

        return [], self._is_speaking

    def force_end(self) -> list[bytes]:
        """强制结束当前语音"""
        if self._is_speaking:
            chunks = self._extract_complete_chunks()
            self._is_speaking = False
            self._buffer.clear()
            return chunks
        return []

    def _calculate_energy(self, audio_data: bytes) -> float:
        """
        计算音频能量（dB）

        使用简化的 RMS 能量计算。
        """
        if len(audio_data) < 2:
            return -100.0

        # 解析 16-bit PCM 采样
        num_samples = len(audio_data) // 2
        total_energy = 0.0

        for i in range(min(num_samples, 1000)):  # 采样计算，避免全量扫描
            sample = struct.unpack_from("<h", audio_data, i * 2)[0]
            total_energy += sample * sample

        if num_samples == 0 or total_energy == 0:
            return -100.0

        rms = (total_energy / num_samples) ** 0.5
        # 转换为 dB
        if rms < 1:
            rms = 1
        db = 20 * __import__('math').log10(rms)
        return db

    def _extract_complete_chunks(self) -> list[bytes]:
        """提取完整的语音片段"""
        chunks = []
        if len(self._buffer) >= self._min_chunk_bytes:
            chunks.append(bytes(self._buffer))
        self._buffer.clear()
        return chunks


# ============================================================
# ASR 客户端基类
# ============================================================

class ASRClient:
    """ASR 客户端基类"""

    def __init__(self, name: str = "base"):
        self._name = name

    async def transcribe(self, audio_data: bytes) -> str:
        """
        转写音频为文字

        Args:
            audio_data: 音频数据

        Returns:
            str: 转写结果
        """
        raise NotImplementedError

    async def transcribe_stream(
        self,
        audio_stream: AsyncGenerator[bytes, None],
    ) -> AsyncGenerator[str, None]:
        """
        流式转写

        默认实现：累积全部音频帧后调用 transcribe() 一次性转写。
        子类可覆盖为真正的增量流式转写（如按 VAD 片段逐段转写）。

        Args:
            audio_stream: 音频流

        Yields:
            str: 转写结果
        """
        chunks: list[bytes] = []
        async for chunk in audio_stream:
            if chunk:
                chunks.append(chunk)

        if not chunks:
            return

        audio_data = b''.join(chunks)
        text = await self.transcribe(audio_data)
        if text:
            yield text

    @property
    def name(self) -> str:
        return self._name


# ============================================================
# FunASR 客户端
# ============================================================

class FunASRClient(ASRClient):
    """
    阿里 FunASR ASR 客户端

    需要安装：pip install funasr
    使用 paraformer-zh 模型，中文优化。
    """

    def __init__(self, model_name: str = "paraformer-zh"):
        super().__init__("funasr")
        self._model_name = model_name
        self._model = None

    async def _ensure_model(self):
        """延迟加载模型"""
        if self._model is not None:
            return

        try:
            from funasr import AutoModel
            self._model = AutoModel(model=self._model_name)
            _log.info(f"FunASR 模型已加载: {self._model_name}")
        except ImportError:
            _log.warning("FunASR 未安装，使用降级方案")
        except Exception as e:
            _log.error(f"FunASR 模型加载失败: {e}")

    async def transcribe(self, audio_data: bytes) -> str:
        await self._ensure_model()

        if self._model is None:
            return self._fallback_transcribe(audio_data)

        try:
            result = self._model.generate(input=audio_data)
            if result and len(result) > 0:
                return result[0].get("text", "")
            return ""
        except Exception as e:
            _log.error(f"FunASR 转写失败: {e}")
            return ""

    def _fallback_transcribe(self, audio_data: bytes) -> str:
        """降级方案：返回占位文本"""
        _log.debug("FunASR 降级：返回占位文本")
        return "[语音识别功能暂不可用]"


# ============================================================
# Whisper 客户端
# ============================================================

class WhisperClient(ASRClient):
    """
    faster-whisper ASR 客户端

    需要安装：pip install faster-whisper
    使用 small 模型，int8 量化，CPU 可用。
    """

    def __init__(
        self,
        model_size: str = "small",
        device: str = "cpu",
        compute_type: str = "int8",
    ):
        super().__init__("whisper")
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._model = None

    async def _ensure_model(self):
        """延迟加载模型"""
        if self._model is not None:
            return

        try:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(
                self._model_size,
                device=self._device,
                compute_type=self._compute_type,
            )
            _log.info(
                f"Whisper 模型已加载: {self._model_size}"
            )
        except ImportError:
            _log.warning("faster-whisper 未安装")
        except Exception as e:
            _log.error(f"Whisper 模型加载失败: {e}")

    async def transcribe(self, audio_data: bytes) -> str:
        await self._ensure_model()

        if self._model is None:
            return self._fallback_transcribe(audio_data)

        try:
            segments, _ = self._model.transcribe(
                audio_data,
                language="zh",
            )
            text = "".join(seg.text for seg in segments)
            return text
        except Exception as e:
            _log.error(f"Whisper 转写失败: {e}")
            return ""

    def _fallback_transcribe(self, audio_data: bytes) -> str:
        _log.debug("Whisper 降级：返回占位文本")
        return "[语音识别功能暂不可用]"


# ============================================================
# 语音管线
# ============================================================

class VoicePipeline:
    """
    全双工语音管线

    流程：
      1. 接收前端 WebSocket 传来的 PCM 音频流
      2. VAD 检测语音起止
      3. ASR 转写
      4. 回调上层处理

    两种模式：
      - 后端 ASR：接收二进制音频帧
      - 前端 ASR：接收文本帧（浏览器已识别）
    """

    def __init__(
        self,
        asr_client: ASRClient | None = None,
        on_text: Callable[[str], None] | None = None,
        sample_rate: int = 16000,
    ):
        """
        Args:
            asr_client: ASR 客户端（None 则使用前端 ASR 模式）
            on_text: 文本回调函数
            sample_rate: 采样率
        """
        self._asr = asr_client
        self._on_text = on_text
        self._vad = SimpleVAD(sample_rate=sample_rate)
        self._active = False

    async def start(self):
        """启动管线"""
        self._active = True
        self._vad.reset()
        _log.info(f"语音管线启动: asr={self._asr.name if self._asr else 'front'}")

    async def stop(self):
        """停止管线"""
        self._active = False
        # 强制结束最后一段
        chunks = self._vad.force_end()
        if chunks and self._asr:
            for chunk in chunks:
                text = await self._asr.transcribe(chunk)
                if text and self._on_text:
                    self._on_text(text)
        _log.info("语音管线停止")

    async def process_audio(self, audio_data: bytes):
        """
        处理二进制音频帧（后端 ASR 模式）

        Args:
            audio_data: PCM 音频数据
        """
        if not self._active:
            return

        # VAD 处理
        chunks, is_speaking = self._vad.process_audio(audio_data)

        # 处理完整的语音片段
        for chunk in chunks:
            if self._asr:
                text = await self._asr.transcribe(chunk)
                if text and self._on_text:
                    self._on_text(text)
                    _log.debug(f"ASR 转写: '{text[:30]}'")

    async def process_frontend_text(self, text: str):
        """
        处理前端 ASR 文本帧（前端 ASR 模式）

        Args:
            text: 浏览器识别的文本
        """
        if not self._active or not text:
            return

        if self._on_text:
            self._on_text(text)
            _log.debug(f"前端 ASR: '{text[:30]}'")

    @property
    def is_active(self) -> bool:
        return self._active
