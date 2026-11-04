// 喵艺前端纯函数测试 · 入口：先打包测试目标，再运行 node --test（显式列出测试文件）
import { spawnSync } from 'node:child_process'
import { readdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))

const build = spawnSync(process.execPath, [path.join(here, 'build.mjs')], { stdio: 'inherit' })
if (build.status !== 0) process.exit(build.status)

const testFiles = readdirSync(here)
  .filter((f) => f.endsWith('.test.mjs'))
  .map((f) => path.join(here, f))

const test = spawnSync(process.execPath, ['--test', ...testFiles], { stdio: 'inherit' })
process.exit(test.status ?? 1)
