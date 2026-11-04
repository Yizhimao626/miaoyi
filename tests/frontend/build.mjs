// 喵艺前端纯函数测试 · 打包器（零新增依赖：复用 frontend/node_modules 里的 esbuild）
// 把待测 TS 模块打包成 ESM 供 node:test 直接 import。
import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const require = createRequire(import.meta.url)
const here = path.dirname(fileURLToPath(import.meta.url))
const esbuild = require(path.join(here, '../../frontend/node_modules/esbuild'))
const src = path.join(here, '../../frontend/src')
const outdir = path.join(here, '.build')
const stubs = path.join(here, 'stubs')

// tts.ts 的运行时依赖重定向到 stub（Node 环境无 fetch/Audio/localStorage）
const ttsStubPlugin = {
  name: 'tts-stubs',
  setup(b) {
    b.onResolve({ filter: /utils\/api$/ }, () => ({ path: path.join(stubs, 'api.mjs') }))
    b.onResolve({ filter: /utils\/config$/ }, () => ({ path: path.join(stubs, 'config.mjs') }))
    b.onResolve({ filter: /utils\/storage$/ }, () => ({ path: path.join(stubs, 'storage.mjs') }))
  },
}

const targets = [
  { entry: 'utils/sticker.ts', out: 'sticker.mjs', plugins: [] },
  { entry: 'utils/emotionAvatar.ts', out: 'emotionAvatar.mjs', plugins: [] },
  { entry: 'utils/messageGroup.ts', out: 'messageGroup.mjs', plugins: [] },
  { entry: 'services/tts.ts', out: 'tts.mjs', plugins: [ttsStubPlugin] },
]

for (const t of targets) {
  await esbuild.build({
    entryPoints: [path.join(src, t.entry)],
    outfile: path.join(outdir, t.out),
    bundle: true,
    format: 'esm',
    platform: 'neutral',
    target: 'node18',
    plugins: t.plugins,
    logLevel: 'silent',
  })
  console.log('bundled', t.out)
}
