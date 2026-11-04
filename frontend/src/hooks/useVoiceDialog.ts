/**
 * 喵艺 · 通话内语音对话 hook（后端 ASR）
 * 通话中：MediaRecorder 持续录音 → 分段发二进制音频到后端 → Whisper 转写 → 喵艺回复
 *
 * 【后端 ASR】替换原 Web Speech API（依赖 Google 服务器，中国大陆网络报 network error
 * 导致"说话没反应"）。改用 MediaRecorder 录音 + 后端 faster-whisper 离线识别，彻底摆脱 Google 依赖。
 *
 * 【barge-in 语音打断】Silero VAD（@ricky0123/vad-web，模型/ORT wasm 本地化到 public/vad/）：
 * 喵艺播报期间检测到用户开口 → 立即停止播放（__bargeInCall）+ 通知后端作废旧回合（barge_in），
 * 实现"一开口喵艺就闭嘴"的真实施电话体验。
 */
import { useEffect, useRef } from 'react'
import { MicVAD } from '@ricky0123/vad-web'
import { useCallStore } from '../store/callStore'

const w = window as unknown as {
  __sendCallWs?: (obj: unknown) => void
  __botSpeaking?: boolean
  __botSpeakingSince?: number
  __bargeInCall?: () => void
}

export function useVoiceDialog() {
  const status = useCallStore((s) => s.status)
  const micOn = useCallStore((s) => s.micOn)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const vadRef = useRef<Awaited<ReturnType<typeof MicVAD.new>> | null>(null)
  // 【降级】VAD 初始化失败（如极老设备不支持 SIMD）只标记一次，通话其余功能不受影响
  const vadFailedRef = useRef(false)
  // 【竞态修复】麦克风会话代次：挂断/关麦后旧的 getUserMedia promise 才 resolve 时，
  // 检测代次失配即立即释放流，杜绝"麦克风常亮/音频双份上传"泄漏
  const micGenRef = useRef(0)

  useEffect(() => {
    if (status !== 'connected') {
      stop()
      return
    }
    if (micOn) {
      start()
    } else {
      stop()
    }
    return stop
  }, [status, micOn])

  function start() {
    if (recorderRef.current) return
    const gen = ++micGenRef.current
    const sendCallWs = w.__sendCallWs
    if (!sendCallWs) {
      console.warn('[useVoiceDialog] __sendCallWs 未就绪，无法录音')
      return
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      // 【录音修复】非安全上下文（http://IP 或 androidScheme:http）下 mediaDevices 不可用。
      // 通过 window 暴露错误，供 CallScreen 展示给用户。
      console.warn('[useVoiceDialog] 当前环境不支持麦克风（需 HTTPS 或 localhost 访问）')
      ;(window as any).__callMicError = '当前环境不支持麦克风录音，请使用 HTTPS 或 localhost 访问'
      return
    }
    // 【barge-in】显式开启浏览器回声消除/降噪/自动增益：
    // AEC 会从麦克风信号中减去扬声器外放的喵艺声音，是"免耳机打断"的前提
    navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    })
      .then((stream) => {
        // 【竞态修复】gUM 等待期间已挂断/关麦/重新 start → 代次失配，立即释放本次流
        if (gen !== micGenRef.current || useCallStore.getState().status !== 'connected') {
          stream.getTracks().forEach((t) => t.stop())
          return
        }
        ;(window as any).__callMicError = ''
        streamRef.current = stream
        const recorder = new MediaRecorder(stream)
        recorderRef.current = recorder
        recorder.ondataavailable = (e: BlobEvent) => {
          if (e.data && e.data.size > 0) {
            // 【回声防护】喵艺播报期间的录音片段含 TTS 外放回声，整段丢弃不转写。
            // 用户插话打断由 VAD 负责：打断后 __botSpeaking 复位，后续片段正常发送。
            if (w.__botSpeaking) return
            e.data.arrayBuffer().then((buf: ArrayBuffer) => {
              sendCallWs({ type: 'audio', bytes: buf })
            })
          }
        }
        // 每 3 秒产出一个音频片段，发送后端转写（无需停麦，持续录音）
        recorder.start(3000)
        void initVAD(stream, gen)
      })
      .catch((err) => {
        console.warn('[useVoiceDialog] 麦克风权限被拒:', err)
        // 【提示接线】权限被拒也暴露给 CallScreen 展示（旧版两处失败路径只有一处写入）
        ;(window as any).__callMicError = '麦克风权限被拒绝，请在浏览器/系统设置中允许麦克风访问'
      })
  }

  /** 初始化 Silero VAD（模型 + ONNX wasm 均本地化，无 CDN 依赖） */
  async function initVAD(stream: MediaStream, gen: number) {
    if (vadRef.current || vadFailedRef.current) return
    try {
      const vad = await MicVAD.new({
        stream,
        // 资产本地化：public/vad/（silero_vad.onnx + worklet）+ public/vad/ort/（ort-wasm-simd.wasm）
        workletURL: '/vad/vad.worklet.bundle.min.js',
        modelURL: '/vad/silero_vad.onnx',
        ortConfig: (ort) => {
          ort.env.wasm.wasmPaths = '/vad/ort/'
        },
        // 打断要快：连续 2 帧（约 192ms）正判即触发
        positiveSpeechThreshold: 0.6,
        negativeSpeechThreshold: 0.45,
        minSpeechFrames: 2,
        onSpeechStart: () => {
          // 仅在喵艺播报期间（且避开开口后 600ms 静默窗防误触）才打断
          if (w.__botSpeaking && Date.now() - (w.__botSpeakingSince || 0) > 600) {
            console.log('[useVoiceDialog] barge-in：检测到用户插话，打断喵艺播报')
            w.__bargeInCall?.()
            w.__sendCallWs?.({ type: 'barge_in' })
          }
        },
      })
      // 【竞态修复】模型加载期间已挂断/重新开始 → 代次失配，销毁本实例防泄漏
      if (gen !== micGenRef.current) {
        try { vad.destroy() } catch { /* ignore */ }
        return
      }
      vad.start()
      vadRef.current = vad
    } catch (e) {
      console.warn('[useVoiceDialog] Silero VAD 初始化失败，语音打断不可用（通话其余功能正常）', e)
      vadFailedRef.current = true
    }
  }

  function stop() {
    // 【竞态修复】先递增代次：使在途的 getUserMedia/VAD 初始化 promise 失效
    micGenRef.current++
    const r = recorderRef.current
    if (r && r.state !== 'inactive') {
      try { r.stop() } catch { /* ignore */ }
    }
    recorderRef.current = null
    // 【barge-in】销毁 VAD（会关闭其自有 AudioContext，不动我们的麦克风流）
    const vad = vadRef.current
    if (vad) {
      try { vad.destroy() } catch { /* ignore */ }
    }
    vadRef.current = null
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop())
      streamRef.current = null
    }
  }
}
