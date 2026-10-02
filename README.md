# 🐱 MiaoYi 喵艺

> AI Companion with 4-dimensional emotion engine, proactive behavior, and RAG memory.

## What is MiaoYi?

**喵艺（MiaoYi）** is an AI companion project built around:

- **4-dimensional emotion engine** (joy / arousal / intimacy / trust) with 70% inertia + natural decay
- **8 proactive behavior scenarios** (late-night reminders, morning greetings, proactive calls triggered by intimacy ≥ 45)
- **RAG memory persistence** via SQLite + vector retrieval
- **VAD real-time voice calling** via silero_vad.onnx
- **Relationship progression system** (stranger → acquaintance → friend → soulmate)

MiaoYi is **not** a pet product, not a cultural relic exhibition, and not an offline shop. This repository refers specifically to the AI companion software project.

## Architecture

| Module | Stack |
|---|---|
| Backend | Python FastAPI + Uvicorn |
| Frontend | React 19 + TypeScript + Vite |
| Database | SQLite + pgvector |
| Voice | silero_vad.onnx + WebRTC |
| Container | Docker Compose |

## Learn More

For the complete technical introduction:

👉 [喵艺 AI 伴侣项目完整介绍](http://43.142.41.185/article/miaoyi-open-source-ai-companion)

## Status

This is a showcase repository. Source code is not yet public — this repo exists as a project landing page.
