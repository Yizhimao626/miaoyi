/**
 * 同步 VAD 离线资产到 public/vad/（构建产物，不入库）
 *
 * 来源（均在 npm 依赖里，由 npm 管理版本与许可证）：
 *   @ricky0123/vad-web  → dist/silero_vad.onnx
 *   onnxruntime-web     → dist/ort-wasm-simd.wasm
 *
 * 这两个二进制共约 11 MB（wasm 9.5 MB + 模型 1.8 MB），不放进 Git 仓库；
 * dev / build 前自动复制到 public/vad/，运行时仍从本地路径加载，无 CDN 依赖。
 * 许可证信息见仓库根目录 THIRD_PARTY_NOTICES.md。
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const outDir = path.join(root, 'public', 'vad')

const assets = [
  {
    src: path.join(root, 'node_modules', '@ricky0123', 'vad-web', 'dist', 'silero_vad.onnx'),
    dst: path.join(outDir, 'silero_vad.onnx'),
  },
  {
    src: path.join(root, 'node_modules', 'onnxruntime-web', 'dist', 'ort-wasm-simd.wasm'),
    dst: path.join(outDir, 'ort', 'ort-wasm-simd.wasm'),
  },
]

for (const { src, dst } of assets) {
  if (!fs.existsSync(src)) {
    console.error(`[vad] 缺少 ${path.relative(root, src)}，请先运行 npm ci（或 npm install）`)
    process.exit(1)
  }
  fs.mkdirSync(path.dirname(dst), { recursive: true })
  fs.copyFileSync(src, dst)
  console.log(`[vad] ${path.relative(root, src)} → ${path.relative(root, dst)}`)
}
