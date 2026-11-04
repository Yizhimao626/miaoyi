# Architecture

**English** · [简体中文](ARCHITECTURE.zh-CN.md)

This document describes Miaoyi's core layers and the key request paths, so you can find the relevant code quickly.

## 1. Layers

```
Client (React / Capacitor Android)
        │  REST + WebSocket
        ▼
API layer (server.py)          ── auth, sessions, memory, calls, admin routes
        │
        ▼
Engine layer (core/)           ── dialogue, emotion, memory, persona, LLM router, speech, proactive care
        │
        ▼
Service layer (services/, utils/) ── message orchestration, logging, config, session state
        │
        ▼
Data layer (db_manager.py)     ── SQLite / MySQL abstraction, single read/write entry point
```

Design rule: **the API layer only does protocol translation and authentication; all business decisions live in `core/`**. The same engines serve both HTTP and WebSocket, so adding another protocol later does not require duplicating logic.

## 2. The path of one conversation turn

1. The client sends a user message over WebSocket (the HTTP endpoint is supported as a fallback)
2. The dialogue engine assembles context: system persona + emotion state + retrieved memories + the last few turns
3. **LLM routing** picks a model per role:
   - `emotion`: classify the user's mood, return structured JSON (fast model)
   - `light`: intent classification (needs search? tool call?)
   - `chat`: generate the reply the user actually reads (reasoning model, streaming)
4. Streamed tokens are pushed back chunk by chunk
5. After the turn, emotion state is updated asynchronously, memories are written, and proactive care may be triggered

## 3. LLM provider pool

- Providers are declared in `llm.providers` in `config.yaml`; `api_key_env` points at the environment variable name
- Providers without a configured key are skipped at startup
- A provider that keeps failing enters a **60 s cooldown**, during which requests go to the next available provider
- When the caller passes an explicit `model`, it only applies to the primary provider; fallbacks use each provider's own default model (mixing vendor-specific model names causes HTTP 400)

## 4. Emotion and memory

- **Emotion state**: `joy` / `arousal` / `intimacy` / `trust` are persisted per user with natural decay and injected into the system prompt, influencing tone and reply length
- **Memory**: every memory record carries an emotion tag and a timestamp; retrieval considers both semantic similarity and emotional relevance
- **Memory cards**: retrieval results are returned to the frontend as visual cards, so the user can feel that "it remembers me"

## 5. Call pipeline

```
Client taps the call button
  → POST /call/dial (creates a call session, returns call_id)
  → WebSocket connects (must carry a valid token matching the path user_id)
  → Client records audio and uploads it in chunks
  → Backend ASR (faster-whisper) transcribes
  → Dialogue engine generates a reply
  → TTS synthesis → binary audio frames pushed back
  → Client decodes and plays via AudioContext
```

Two engineering details worth knowing:

- **Mobile autoplay restrictions**: AudioContext must be `resume()`d inside a user gesture, otherwise the call connects but stays silent
- **TTS fallback chain**: CosyVoice2 → GPT-SoVITS → Edge TTS → Baidu TTS → local → gTTS; any unavailable tier degrades automatically, and empty audio is never pushed to the client

## 6. Data model (simplified)

| Table / file | Purpose |
|---|---|
| `users` | Accounts, authentication, roles |
| `sessions` / `messages` | Sessions and message history |
| `memories` | Memory records (text, emotion tag, vector, timestamp) |
| `emotion_state` | Mood, intimacy, growth |
| `data/*.json` | Lightweight runtime state (cache, settings, user preferences) |

## 7. Deployment shapes

- Single VM: systemd runs `server.py`, FastAPI listens directly (no nginx), and the backend serves the built frontend
- Containers: `docker compose up -d`, with separate volumes for data, backups, and logs
- Android: Capacitor wraps the web app in a WebView; the APK is distributed by the backend at `/download/latest` and checks for updates via `/api/version/check`
