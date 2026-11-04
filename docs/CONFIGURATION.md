# Configuration

**English** · [简体中文](CONFIGURATION.zh-CN.md)

Miaoyi is configured through two files:

| File | Holds | Committed to Git? |
|---|---|---|
| `.env` | Secrets and deployment switches (API keys, JWT secret, database password) | No — copy from `.env.example` |
| `config.yaml` | Behaviour: models, memory, voice, proactive care, ports | No — copy from `config.example.yaml` |

`config.yaml` wins for behaviour; `.env` wins for anything security related. Values in `.env` that map to `config.yaml` (such as `MIAOYI_DB_TYPE`) override the YAML.

---

## 1. Minimum setup

```bash
cp config.example.yaml config.yaml
cp .env.example .env

# JWT_SECRET_KEY is mandatory — the process refuses to start without it
python -c "import secrets; print(secrets.token_hex(32))"   # paste into .env
```

That is enough to run: with **no LLM key at all** the backend starts in offline demo mode, and register → login → chat → memory still works end to end. Add one key to switch to real models.

---

## 2. Environment variables

### Runtime

| Variable | Default | Purpose |
|---|---|---|
| `ENVIRONMENT` | `development` | `production` disables `/docs`, `/openapi.json` and guest-only helpers; also tightens cookies |
| `ALLOWED_ORIGINS` | localhost set | Comma-separated CORS origins. **Must include `http://localhost,https://localhost,capacitor://localhost`** for the Android build — Capacitor's WebView origin is `http://localhost` |
| `MAX_BODY_SIZE` | `1048576` (1 MiB) | Max HTTP request body. The example file raises it to 6 MiB for long histories |
| `MIAOYI_DB_TYPE` | from `config.yaml` | `sqlite` or `mysql`, overrides `database.type` |

### LLM keys (all optional)

| Variable | Used for |
|---|---|
| `ZHIPU_API_KEY` | GLM provider; also vision (`glm-4v-flash`) and embeddings (`embedding-3`) |
| `DEEPSEEK_API_KEY` | DeepSeek provider (reply model in the example config) |
| `DASHSCOPE_API_KEY` | Qwen via DashScope; also CosyVoice2 streaming TTS |
| `MOONSHOT_API_KEY` | Moonshot / Kimi |
| `MIAOYI_MOCK_LLM` | `1` force offline demo mode, `0` force disable; empty = auto (on in non-production when no key exists) |

Providers whose key is missing are **skipped silently** — you do not need to fill the whole list.

### Security

| Variable | Default | Purpose |
|---|---|---|
| `JWT_SECRET_KEY` | placeholder | **Required.** Signing key for access/refresh tokens |
| `INVITE_CODE` | empty | When set, registration requires a matching invite code (otherwise registration is open) |
| `REQUEST_SIGNING_ENABLED` | `false` | Enables `x-signature` / `x-timestamp` / `x-nonce` request signing |
| `REQUEST_TIMESTAMP_TOLERANCE` | `300` | Replay window in seconds for signed requests |
| `CLEAR_NON_ADMIN_ON_STARTUP` | `false` | Dev convenience switch; never enable in production |

### Database (only for MySQL)

`MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE`.
Set `database.type: mysql` in `config.yaml` (or `MIAOYI_DB_TYPE=mysql`), then run `python migrations/sqlite_to_mysql.py` to move existing SQLite data.

### Optional memory micro-service

`MEMORY_TOKEN` — shared secret between `server.py` and `memory_server.py` when `memory.remote.enabled: true`.

---

## 3. config.yaml in detail

### 3.1 LLM provider pool

```yaml
llm:
  providers:
    - provider: zhipu
      api_key_env: ZHIPU_API_KEY
      model_name: glm-4-flash
    - provider: openai-compat
      api_key_env: DASHSCOPE_API_KEY
      api_base: https://dashscope.aliyuncs.com/compatible-mode/v1
      model_name: qwen-plus
```

- `provider` is either `zhipu` (native SDK) or `openai-compat` (any OpenAI-compatible endpoint — DeepSeek, DashScope, Moonshot, SiliconFlow, vLLM…)
- `api_base` is required for `openai-compat`
- Order matters: the list is tried top-down. A provider that returns 429/5xx/timeout goes into a **60 s cooldown** and the next one is used
- `min_max_tokens` (optional) raises the caller's token budget for reasoning-style models, whose `reasoning_content` can otherwise eat the whole budget

**Adding a provider** is two steps — append an entry and export the key:

```yaml
    - provider: openai-compat
      api_key_env: MY_VENDOR_KEY
      api_base: https://api.example.com/v1
      model_name: my-model
```

### 3.2 Role routing

Every internal "brain" asks the pool for a model by role:

| Role | Used by | Suggested model |
|---|---|---|
| `chat` | Main streaming reply | Strongest model you can afford |
| `light` | Intent classification, small talk fallback | Cheap model |
| `inner` | Inner monologue before speaking | Cheap model |
| `think` | Deep reasoning step | Cheap or strong, your call |
| `emotion` | Emotion analysis plus image "feelings" | Cheap model |
| `tool` | Tool decision (weather / time / search) | Cheap model |
| `memory` | Memory quality gate (reserved) | Cheap model |
| `proactive` | Proactive message drafting (reserved) | Cheap model |

```yaml
  roles:
    chat:      {model: deepseek-flash}
    light:     {model: glm-4-flash}
    # …
```

Rules that surprise people:

- A role's `model` is honoured only if a provider with that **exact `model_name`** exists in `providers` **and** has a key. Otherwise it falls back to the primary provider.
- When the caller passes an explicit model, role routing is bypassed for that call; fallbacks use each provider's own `model_name`. Keeping vendor-specific names out of fallback paths is deliberate — mixing them causes HTTP 400 chains.

### 3.3 Vision and embeddings

```yaml
  vision:
    provider: openai-compat
    api_key_env: ZHIPU_API_KEY
    api_base: https://open.bigmodel.cn/api/paas/v4
    model_name: glm-4v-flash
  embeddings:
    enabled: true
    api_key_env: ZHIPU_API_KEY
    model: embedding-3
```

- Without a vision key the app degrades to "I can't see images" instead of failing the message
- Without embeddings, memory falls back to keyword retrieval — the chat still works

### 3.4 Voice

```yaml
voice:
  enabled: true
  asr_engine: whisper          # faster-whisper
  whisper_model: base
  tts_engine: edge             # edge | gptsovits | cosyvoice
  cosyvoice_enabled: true      # needs DASHSCOPE_API_KEY
  gptsovits_url: ''            # e.g. http://127.0.0.1:9880
  wake_word_enabled: false
```

The TTS chain is **CosyVoice2 → GPT-SoVITS → Edge TTS → Baidu → local → gTTS**; each tier that is unavailable or returns empty audio is skipped automatically. Set `local_tts_enabled: false` on servers with no audio device.

### 3.5 Memory

| Key | Default | Meaning |
|---|---|---|
| `max_history` | 50 | Turns kept in the prompt window |
| `memory_retention_days` | 90 | Soft retention for memory records |
| `vector_top_k` | 3 | Memories retrieved per turn |
| `vector_threshold` | 0.7 | Minimum similarity |
| `cache_size` | 50 | In-process cache entries |
| `remote.enabled` | false | Route memory through `memory_server.py` over HTTP |

With `remote.enabled: true`, start `python memory_server.py` first and make sure `MEMORY_TOKEN` matches on both processes. With `false`, memory lives inside the main process (simplest setup).

### 3.6 Proactive care

| Key | Default | Meaning |
|---|---|---|
| `silence_timeout_seconds` | 900 | Inactivity before a nudge is considered |
| `trigger_probability` | 0.3 | Chance per evaluation window |
| `noise_budget_per_hour` | 2 | Hard cap of proactive messages per hour |
| `dnd_start` / `dnd_end` | 23 / 8 | Do-not-disturb window (crosses midnight); suppressed urges merge into a morning message |
| `moments_enabled` | true | Simulated social feed posts |
| `moments_interval_hours` | 3 | Spacing between posts |
| `moments_max_per_day` | 4 | Daily cap |

`noise_budget_per_hour` and the DND window are the two settings that keep the companion from becoming spam.

### 3.7 Other sections

| Section | Notable keys |
|---|---|
| `server` | `host`, `port` (default 8001), `reload`, `max_port_try` |
| `database` | `type`, `mysql.{host,port,user,password,database}` |
| `features` | `emotional_memory_enabled`, `proactive_chat_enabled`, `style_mimicry_enabled`, `voice_input_enabled`, `voice_output_enabled`, `deep_thinking_enabled`, `autobiography_enabled` |
| `emotion` | `emotional_independence`, `empathy_coefficient`, `negative_window_minutes`, `redirection_threshold` |
| `search` | `enabled`, `sources`, `max_results`, `timeout` |
| `security` | `max_message_length` (default 2000), `sensitive_word_enabled` |
| `performance` | `thread_pool_size`, `max_concurrent_thinking`, `thinking_timeout_seconds`, `cache_ttl_hours` |
| `appearance` | Theme colours for the web client |
| `logging` | `level`, `max_bytes`, `backup_count` |

---

## 4. Cost control recipes

1. **Cheapest usable setup** — one free key (Zhipu `glm-4-flash`) for every role; vision and embeddings reuse the same key.
2. **Best quality per yuan** — a stronger model on `chat` only, cheap model on the other seven roles. That is the shape of the example config.
3. **Zero cost** — leave all keys empty and run in demo mode; or set `MIAOYI_MOCK_LLM=1` explicitly.
4. **Freeze the bleed** — lower `noise_budget_per_hour`, raise `silence_timeout_seconds`, lower `max_history`, and set `llm.roles.inner` to the cheapest model (inner monologue fires often).

---

## 5. Troubleshooting

| Symptom | Likely cause |
|---|---|
| Process exits at startup with a security message | `JWT_SECRET_KEY` missing or left at the placeholder |
| Login succeeds in logs but the app shows nothing (Android) | CORS: add `http://localhost`, `https://localhost`, `capacitor://localhost` to `ALLOWED_ORIGINS` |
| Replies look like canned demo text | No provider key found → demo mode. Check `ZHIPU_API_KEY` etc., or set `MIAOYI_MOCK_LLM=0` |
| All requests 429 from one vendor | That provider is cooling down; add a second provider to the pool |
| Call connects but no audio | AudioContext not resumed inside a user gesture, or every TTS tier unavailable |
| Memory retrieval feels random | Embedding key missing (keyword fallback) or `vector_threshold` too low |
| Port 8001 silently changes | An old process still holds the port — kill it before restarting |
