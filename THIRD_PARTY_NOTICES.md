# Third-party notices / 第三方组件声明

喵艺（Miaoyi）本体以 [AGPL-3.0](LICENSE) 发布。下列第三方组件随本仓库或构建产物一起分发，各自保留原有许可证。

## 随构建产物分发的组件

| 组件 | 版本 | 许可证 | 用途 | 在本仓库中的位置 |
|---|---|---|---|---|
| [onnxruntime-web](https://github.com/microsoft/onnxruntime) | 1.14.0 | MIT | 浏览器端 ONNX 推理（语音活动检测） | 不提交二进制；构建前由 `frontend/scripts/sync-vad-assets.mjs` 从 `node_modules` 复制 `ort-wasm-simd.wasm` 到 `frontend/public/vad/ort/` |
| [@ricky0123/vad-web](https://github.com/ricky0123/vad) | 0.0.17 | ISC | 浏览器端 VAD 封装 | `frontend/public/vad/vad.worklet.bundle.min.js`；`silero_vad.onnx` 同样由上述脚本复制 |
| [Silero VAD](https://github.com/snakers4/silero-vad) | — | MIT | VAD 模型 `silero_vad.onnx` | 由 `@ricky0123/vad-web` 分发 |

其余运行时依赖（React、React DOM、zustand 等，均为 MIT）的完整许可证文本，可在 `cd frontend && npm ci` 之后于 `frontend/node_modules/<package>/LICENSE` 查阅；版本锁定见 `frontend/package-lock.json`。

## MIT License

Copyright (c) Microsoft Corporation (onnxruntime-web)
Copyright (c) the Silero VAD authors (Silero VAD)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## ISC License

Copyright (c) the @ricky0123/vad authors

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
