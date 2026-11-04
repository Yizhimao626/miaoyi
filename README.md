# Miaoyi · An AI Companion That Feels, Remembers, and Talks

> A full-stack AI companion app with an emotion engine, long-term memory, real-time voice calls, and proactive care.
> Built with **FastAPI + React 19 + WebSocket**, and shipped as both a **web app** and an **Android APK** through Capacitor.

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=white)](https://react.dev/)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

<p align="center">
  <img src="docs/screenshots/demo.gif" width="26%" alt="Live chat demo" />
</p>
<p align="center"><em>Live demo — send a message, watch the streaming reply (16 s, recorded from the running app)</em></p>

<p align="center">
  <img src="docs/screenshots/home.png" width="24%" alt="Home" />
  <img src="docs/screenshots/chat.png" width="24%" alt="Chat" />
  <img src="docs/screenshots/memory.png" width="24%" alt="Memory" />
  <img src="docs/screenshots/login.png" width="24%" alt="Login" />
</p>
<p align="center"><em>Home · Chat · Memory · Login — captured from the running app</em></p>

---

## Why this project is worth a look

Most "AI chatbot" demos stop at *send a message → get a reply*. Miaoyi is built as a small product, and the interesting part is everything around the reply:

1. **Dual-brain LLM routing** — a cheap, fast model handles intent classification and emotion analysis as JSON; a stronger reasoning model writes the actual reply. Providers sit in a pool with role-based routing and automatic failover (60 s cooldown per provider).
2. **An emotion engine that changes behavior** — mood, intimacy, and growth level are persisted per user and injected into every prompt, so the same input does not always produce the same answer.
3. **End-to-end voice calls** — binary audio over WebSocket, faster-whisper for ASR, and a TTS fallback chain (CosyVoice2 → GPT-SoVITS → Edge TTS → Baidu → gTTS) that degrades gracefully instead of failing.
4. **Memory with retrieval** — embeddings + emotion tags, retrieved per turn and surfaced as memory cards in the UI.
5. **Proactive care** — a scheduler decides when to reach out, so the app is not purely reactive.
6. **One codebase, three targets** — React web, Capacitor Android APK (with in-app update), and an optional Electron desktop shell.

---

## Architecture

```mermaid
flowchart LR
    subgraph Clients
        WEB[Web · React 19 + Vite]
        APK[Android APK · Capacitor]
    end

    subgraph Backend["FastAPI backend"]
        API[REST API<br/>auth · sessions · memory · admin]
        WS["WebSocket<br/>chat stream · call audio"]
        DE[Dialogue Engine]
        EM[Emotion Engine<br/>mood · intimacy · growth]
        MS[Memory Service<br/>RAG + emotion tags]
        CM[Call Manager<br/>ASR · TTS · turn state machine]
        PE[Proactive Engine]
    end

    ROUTER["LLM Router<br/>role → model, cooldown failover"]
    DB[(SQLite / MySQL)]
    TTS["TTS chain<br/>CosyVoice2 → GPT-SoVITS → Edge → Baidu"]

    WEB --> API
    APK --> API
    WEB --> WS
    APK --> WS
    API --> DE
    WS --> DE
    WS --> CM
    DE --> EM
    DE --> MS
    DE --> ROUTER
    CM --> ROUTER
    CM --> TTS
    MS --> DB
    EM --> DB
    DE --> DB
    PE --> DE
```

---

## Measured numbers

Measured on a 2 vCPU / 2 GB cloud VM, single user, warm connection:

| Metric | Fast path (light tasks) | Main reply path |
|---|---|---|
| First token | 0.7 – 1.0 s | 1.5 – 1.9 s |
| Reply length | 8 – 13 tokens | 119 – 150 tokens |
| Provider failover | automatic, 60 s cooldown | automatic, 60 s cooldown |

The fast model is intentionally kept for latency-sensitive subtasks (intent, emotion JSON, tool routing); the reasoning model is reserved for the text the user actually reads.

---

## Tech stack

| Layer | Choice |
|---|---|
| Backend | Python 3.10+, FastAPI, WebSocket, SQLite (default) / MySQL (optional) |
| LLM | Zhipu GLM-4-Flash / GLM-4V-Flash, DeepSeek, DashScope Qwen, Moonshot — pluggable pool |
| Offline demo | Built-in mock provider — the full flow runs without any API key |
| Memory | Embedding-based RAG + emotion-tagged memory records |
| Speech | faster-whisper (ASR), CosyVoice2 / GPT-SoVITS / Edge TTS / Baidu TTS / gTTS (TTS) |
| Frontend | React 19, TypeScript, Vite 6, Zustand |
| Mobile | Capacitor 8, Android Gradle (compileSdk 36, minSdk 24, JDK 21) |
| Deploy | systemd / Docker Compose, single VM friendly |

---

## Quick start

```bash
git clone https://github.com/<your-name>/miaoyi.git
cd miaoyi

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp config.example.yaml config.yaml
cp .env.example .env               # optional: add a key for real models

# required once: put a random value into JWT_SECRET_KEY in .env
python -c "import secrets; print(secrets.token_hex(32))"

python server.py                   # backend → http://localhost:8001
```

Frontend in dev mode:

```bash
cd frontend
npm install
npm run dev                        # Vite dev server, proxied to :8001
```

Docker:

```bash
cp .env.example .env               # set JWT_SECRET_KEY; keys optional (demo mode)
docker compose up -d
```

> **No API key?** The backend automatically falls back to *offline demo mode*: replies are generated by a built-in mock provider, so register → login → chat → memory still works end to end without calling any external model. Force it with `MIAOYI_MOCK_LLM=1`, disable it with `MIAOYI_MOCK_LLM=0`.
>
> `JWT_SECRET_KEY` is mandatory: the backend refuses to start without it (security check). `ZHIPU_API_KEY` / `DEEPSEEK_API_KEY` are optional thanks to demo mode.
>
> Speech features additionally need a reachable ASR/TTS backend; otherwise the app falls back to text-only.

---

## Repository layout

```
├── server.py            # FastAPI entry point and HTTP/WS routes
├── db_manager.py        # SQLite/MySQL data access layer
├── app_state.py         # global application state
├── core/                # engines: dialogue, emotion, memory, persona, LLM router, speech…
├── services/            # message service
├── utils/               # logger, config manager, session helpers
├── frontend/            # React 19 app + Capacitor Android project
├── admin/               # optional admin console (Vite)
├── site/                # static pages (privacy policy / terms)
├── docs/                # architecture notes and screenshots
├── scripts/ tests/      # tooling and tests
├── config.example.yaml  # configuration template
└── .env.example         # environment template
```

---

## Roadmap

- [ ] Split `server.py` into `app/api/routes/*` (chat / call / auth / memory / admin)
- [ ] Raise test coverage for the emotion and dialogue engines
- [ ] Publish a public demo instance with a rate-limited guest account
- [x] One-command local stack (compose profile with mock LLM, no API key required)
- [ ] English documentation for the persona / style-mimicry subsystem

---

## About this repository

This is a **sanitized showcase version** of a personal project:

- No API keys, production configuration, or server addresses
- No user data, chat history, or private persona corpora
- No model weights or media assets beyond what is needed to read the code

It is intended for learning and portfolio review. If you plan to run a public instance, review the local regulations that apply to generative AI services in your region.

> **Not included:** persona prompts (`prompts/`), media assets (`assets/`), model weights, and user data. The code runs without them, but the full conversational experience needs your own persona files and at least one LLM API key.

## License

[AGPL-3.0](LICENSE) — you may use, modify and self-host it, but if you run a modified version as a network service, you must publish your changes.

## Security

Please report vulnerabilities privately — see [SECURITY.md](SECURITY.md). Do not open a public issue for anything involving credentials or user data.
