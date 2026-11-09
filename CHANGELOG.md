# Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。版本号与 Git tag 保持一致。

## [Unreleased]

### Added
- CI 全量单元测试与覆盖率报告（`pytest tests --cov`）
- Docker 配置校验与 Dockerfile lint 任务
- Dependabot（pip / npm / GitHub Actions / Docker）与 CodeQL 扫描

### Fixed
- 端到端 smoke 测试在无后端环境下自动跳过，避免 CI 误报
- 主动庆祝测试在缺少私有 persona prompt 时自动跳过

## [1.1.9] - 2026-11-04

首个开源版本。

### Added
- 四维情绪引擎（joy / arousal / intimacy / trust），支持自然衰减
- RAG 记忆检索与情感记忆
- 多供应商模型池与 60 秒冷却故障转移
- 主动关怀（频率上限、夜间免打扰、早安合并）
- 语音降级链与 WebSocket 通话鉴权
- React 19 + Capacitor 安卓端、Electron 桌面端
- Docker / docker-compose 部署与离线 mock 模式（`MIAOYI_MOCK_LLM=1`）
