# API Reference

**English** · [简体中文](API.zh-CN.md)

Base URL in development: `http://localhost:8001`. The production deployment serves the same paths directly from FastAPI (no reverse proxy required).

> Interactive OpenAPI docs are available at `/docs` and `/openapi.json` **in non-production environments only** (`ENVIRONMENT=production` disables both).

---

## 1. Conventions

### Response envelope

Every REST response uses one shape:

```json
{ "code": 200, "data": { }, "message": "ok" }
```

`code` mirrors the HTTP status. Errors put `null` in `data` and a human message in `message` — messages are Chinese, matching the app's persona.

### Authentication

```http
Authorization: Bearer <access_token>
```

Cookies are a fallback for browser clients (`miaoyi_token`, set on login). WebSocket clients pass the token as a query parameter.

### Rate limits

| Endpoint | Limit |
|---|---|
| `POST /api/auth/register` | 5 / minute |
| `POST /api/auth/login` | 10 / minute |
| `POST /api/auth/refresh` | 20 / minute |
| `POST /api/message` | 30 / minute |
| `POST /api/message/stream` | 20 / minute |
| `GET /api/message/history` | 60 / minute |
| `GET /api/search` | 15 / minute |
| `POST /api/speak`, `/api/voice/synthesize` | 20 / minute |
| `GET /api/version/check`, `/download/latest` | 10 / minute |

Free accounts additionally have a **daily cap of 50 chat messages** (counted in memory by the backend process).

---

## 2. Authentication

### `GET /api/auth/config`
Public. Tells the client whether registration needs an invite code.

```json
{ "code": 200, "data": { "invite_code_required": false }, "message": "ok" }
```

### `POST /api/auth/register`
Public. Body:

```json
{ "username": "cxtest1021x", "phone": "13800000000", "password": "Test123456", "email": "", "invite_code": "" }
```

- `username` 3–50 characters, `phone` must match `1[3-9]\d{9}`, password strength is validated
- Returns `201` with `user_id`, `username`, `token`, `access_token`, `refresh_token`

### `POST /api/auth/login`
Public. `username` may be a username **or** a mainland phone number. Returns the same token fields as registration and also sets the `miaoyi_token` cookie.

- Account lockout after repeated failures → `429`
- Disabled account → `403`

```bash
curl -X POST http://localhost:8001/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"cxtest1021x","password":"Test123456"}'
```

### `POST /api/auth/refresh`
Public. Body `{"refresh_token": "..."}` (or the token in the `Authorization` header).

### `POST /api/auth/logout`
Auth required. Revokes the stored refresh token for the caller.

---

## 3. Chat

### `POST /api/message`
The simpler, non-streaming path.

```json
{ "text": "在吗", "context": "" }
```

Response `data`:

```json
{
  "text": "在的在的～",
  "sticker": null,
  "thought": "…",
  "small_thought": "…",
  "emotional_state": { "joy": 0.6, "arousal": 0.4, "intimacy": 0.7, "trust": 0.8, "dominant_emotion": "joy" },
  "thinking_log": [],
  "memory_triggers": [],
  "autobiography_context": "",
  "response_time_ms": 1200,
  "status": "success",
  "timestamp": "2026-01-01T12:00:00"
}
```

Both the user message and the reply are persisted by the backend — the client does not need to write history itself.

### `POST /api/message/stream`
Same body plus optional `client_msg_id` (duplicates within the window return `409`). Responds with **Server-Sent Events**:

```
data: {"type":"typing","status":"typing"}
data: {"type":"thinking_status","content":"🤔 正在分析情感...","status":"thinking"}
data: {"type":"...","text":"..."}
data: {"type":"typing","status":"done"}
```

### Other chat endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/message/history?limit=20&before_message_id=` | Paginated history (`messages`, `has_more`, `count`) |
| `DELETE` | `/api/message/{message_id}` | Delete one message |
| `POST` | `/api/message/regenerate` | Regenerate the last reply |
| `GET` | `/api/conversations` | Recent conversations (max 20) |
| `DELETE` | `/api/conversation/{session_id}` | Delete one conversation |
| `POST` | `/api/conversation/clear` | Clear all conversations |
| `GET` / `POST` | `/api/session/list`, `/api/session/create` | Session list / create (`{"name": "..."}`) |
| `DELETE` | `/api/session/{session_id}` | Delete a session |
| `POST` | `/api/export/chat` | Export chat history |
| `GET` | `/api/commands/list` | Slash-command catalogue (`/help`, `/status`, `/emotion`, …) |

---

## 4. WebSocket `/ws`

```
ws://localhost:8001/ws?token=<access_token>
```

- The token must be a **valid access token**; a cookie named `miaoyi_token` is accepted as a fallback
- Close code `4001` = authentication failure, `4002` = more than 10 concurrent connections for the same account

### Server → client frames

| `type` | When | Notes |
|---|---|---|
| `greeting` | On connect | Startup greeting text |
| `emotional_state` | On connect, and after turns | Current four-dimensional state |
| `growth_data` | On connect | `level`, `exp`, `exp_max`, `intimacy`, `stage` |
| `typing` | Before a reply | `status: "typing"` / `"done"` |
| `thinking`, `thinking_status`, `thinking_step` | During processing | Thinking trace for the UI |
| `response_chunk` | While streaming | Incremental text |
| `response` | End of turn | Full payload (`text`, `sticker`, `thought`, `emotional_state`, `audio_url`, `client_message_id`, …) |
| `thought_bubble` | Occasionally | Inner monologue bubble |
| `speech` | After ASR | Recognised text from voice input |
| `proactive` | Unprompted | Proactive care message |
| `tts_result` | Reply to a `tts` frame | `audio_url` or `error` |
| `pong` | Reply to `ping` | Heartbeat |
| `error` | On failure | `code` + `message` |

### Client → server frames

| `type` | Fields | Purpose |
|---|---|---|
| `message` | `text`, `context`, `reply_to`, `client_message_id`, `image_url` | Send a chat message (`image_url` must be a local `/uploads-chat/...` path) |
| `ping` | — | Heartbeat, answered with `pong` |
| `tts` | `text` | Synthesise speech, max 12 requests / minute |
| `voice_text` | `text` | ASR text during a call; triggers a call reply |
| `barge_in` | — | User interrupted playback; cancels the in-flight turn |
| `thinking_status` | `content` | Client-side status echo |

Binary frames are raw audio chunks for call ASR (WebM, ≤10 MB each).

```js
const ws = new WebSocket(`ws://localhost:8001/ws?token=${accessToken}`);
ws.onopen = () => ws.send(JSON.stringify({ type: 'message', text: '在吗' }));
ws.onmessage = (e) => {
  const msg = JSON.parse(e.data);
  if (msg.type === 'response_chunk') append(msg.text);
  if (msg.type === 'response') finalize(msg);
};
```

---

## 5. Emotion, relationship, growth

| Method | Path | Returns |
|---|---|---|
| `GET` | `/api/emotion/state` | Current `joy` / `arousal` / `intimacy` / `trust` plus dominant emotion |
| `GET` | `/api/emotion/history` | Recent emotion trajectory |
| `GET` | `/api/emotional_state` | Combined emotional snapshot used by the UI |
| `GET` | `/api/relationship` | `intimacy` and stage name |
| `GET` | `/api/relationship/status` | Full relationship summary |
| `GET` | `/api/relationship/intimacy`, `/api/relationship/anniversary` | Intimacy value / anniversary info |
| `GET` | `/api/growth`, `/api/growth/data` | `level`, `exp`, `exp_max`, `title`, `achievements`, `intimacy` |
| `GET` | `/api/personality`, `POST /api/personality` | Read / update persona settings |
| `POST` | `/api/learn` | Feed a sample for style mimicry |
| `GET` | `/api/character-card/export` | Export the character card |

---

## 6. Memory

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/memory/list?limit=50` | Memory records (`key`, `value`, `created_ts`), 1–500 |
| `POST` | `/api/memory/save` | Save a memory manually |
| `DELETE` | `/api/memory/{key}` | Delete by id or key |
| `PATCH` | `/api/memory/{key}` | Edit a memory |
| `POST` | `/api/memory/{key}/pin` | Pin / unpin |
| `GET` | `/api/memory/search` | Semantic search over memories |
| `GET` | `/api/memory/tags`, `GET /api/memory/tag/{tag}`, `POST /api/memory/tag`, `DELETE /api/memory/tag` | Tag management |
| `POST` | `/api/memory/clear` | `{"days": null}` clears everything; a number clears entries older than N days |
| `GET` | `/api/memory_triggers` | Which memories were triggered recently |

---

## 7. Voice

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/voice_status` | Voice subsystem availability |
| `GET` / `POST` | `/api/voice-mode` | Read / set voice mode (`{"mode": 1}`) |
| `POST` | `/api/speak` | `{"text": "..."}` → `{"audio_url": "/voice_cache/xxx.mp3"}` |
| `POST` | `/api/voice/synthesize` | Alias of `/api/speak` |
| `GET` | `/api/voice/segment?task_id=&index=` | Fetch the N-th TTS segment generated for a streamed reply |

All TTS endpoints return a URL rather than binary data; the audio files are served statically.

---

## 8. Media and files

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/upload/file` | Multipart upload. Images ≤10 MB (jpg/jpeg/png/webp/gif), documents ≤20 MB (pdf/doc/docx/xls/xlsx/ppt/pptx/zip/rar/txt). Images return a `/uploads-chat/...` URL |
| `GET` | `/api/stickers` | Sticker catalogue |
| `GET` | `/api/emoticons/search`, `POST /api/emoticons/create`, `POST /api/sticker/upload` | Sticker search / creation |
| `GET` | `/api/files/exports/{filename}`, `/api/files/uploads/{filename}` | Download generated exports and uploads |

---

## 9. Misc and system

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/version/check?platform=android&version_code=10` | App update check: `has_update`, `force_update`, `download_url`, `file_size` |
| `GET` / `HEAD` | `/download/latest` | Latest APK (anonymous, rate limited) |
| `GET` | `/api/user/profile`, `DELETE /api/user/profile` | Read / delete account |
| `PUT` | `/api/user/password`, `PUT /api/user/nickname` | Credential and nickname updates |
| `POST` | `/api/user/checkin` | Daily check-in (increases intimacy) |
| `GET` | `/api/onboarding`, `/api/daily_fragment` | First-run onboarding and daily fragment |
| `GET` | `/api/config`, `POST /api/config` | Client-visible app settings |
| `GET` | `/api/usage` | Usage counters |
| `GET` | `/api/metrics` | In-process metrics (latency buckets, model distribution) |
| `GET` | `/api/search?query=` | Web/knowledge search (15 / minute) |
| `POST` | `/api/feedback` | User feedback |
| `GET` | `/api/diary/today`, `/api/diary/list`, `/api/diary/memory-lane` | Diary views |
| `GET` | `/api/moments/list`, `POST /api/moments/{id}/like` | Simulated social feed |
| `GET` | `/api/plugins/list`, `POST /api/plugins/{id}/toggle` | Plugin switches |
| `GET` | `/api/holiday`, `/api/time` | Holiday lookup and server time |
| `GET` | `/api/thinking_log`, `/api/autobiography` | Reasoning trace and autobiography |
| `GET` | `/api/push/vapid-key`, `POST /api/push/{subscribe,unsubscribe,notify}` | Web Push |
| `GET` | `/api/admin/llm-pool`, `/api/admin/user/{id}` | Admin-only: pool health and user inspection |

---

## 10. Status codes

| Code | Meaning |
|---|---|
| `200` | Success |
| `201` | Account created |
| `400` | Validation failure (empty message, bad phone, weak password…) |
| `401` | Missing / invalid / expired token, wrong credentials |
| `403` | Disabled account, invalid invite code, missing signature |
| `404` | Unknown conversation, memory or file |
| `409` | Duplicate `client_msg_id` |
| `429` | Rate limit, daily chat quota, or account lockout |
| `500` | Internal error — the reply text is deliberately vague to avoid leaking details |

Clients should treat `401` as "refresh the token once, then log out", and surface `403`/`429` messages verbatim — they carry the real reason (lockout, disabled account, quota).
