# 配置说明

[English](CONFIGURATION.md) · **简体中文**

喵艺由两个文件驱动：

| 文件 | 存放内容 | 是否提交到 Git |
|---|---|---|
| `.env` | 密钥与部署开关（API Key、JWT 密钥、数据库密码） | 否 —— 从 `.env.example` 复制 |
| `config.yaml` | 行为配置：模型、记忆、语音、主动关怀、端口 | 否 —— 从 `config.example.yaml` 复制 |

行为相关以 `config.yaml` 为准；安全相关以 `.env` 为准。`.env` 中能映射到 YAML 的变量（如 `MIAOYI_DB_TYPE`）会覆盖 YAML。

---

## 一、最小配置

```bash
cp config.example.yaml config.yaml
cp .env.example .env

# JWT_SECRET_KEY 必填：没有它进程会直接拒绝启动
python -c "import secrets; print(secrets.token_hex(32))"   # 把输出填进 .env
```

到这里就能跑起来：**一个 Key 都不配**时后端进入离线演示模式，注册 → 登录 → 聊天 → 记忆全链路照常可用。想接真实模型，加一个 Key 即可。

---

## 二、环境变量

### 运行环境

| 变量 | 默认值 | 作用 |
|---|---|---|
| `ENVIRONMENT` | `development` | 设为 `production` 会关闭 `/docs`、`/openapi.json` 并收紧 Cookie |
| `ALLOWED_ORIGINS` | localhost 一组 | 逗号分隔的 CORS 白名单。安卓端**必须**包含 `http://localhost,https://localhost,capacitor://localhost`，因为 Capacitor WebView 的 Origin 就是 `http://localhost` |
| `MAX_BODY_SIZE` | `1048576`（1 MiB） | HTTP 请求体上限；示例文件为长历史放宽到 6 MiB |
| `MIAOYI_DB_TYPE` | 取自 `config.yaml` | `sqlite` 或 `mysql`，覆盖 `database.type` |

### 大模型 Key（全部可选）

| 变量 | 用途 |
|---|---|
| `ZHIPU_API_KEY` | 智谱 GLM 供应商；同时提供视觉（`glm-4v-flash`）与向量（`embedding-3`） |
| `DEEPSEEK_API_KEY` | DeepSeek 供应商（示例配置中的说话主力） |
| `DASHSCOPE_API_KEY` | 阿里云百炼 Qwen；也可用于 CosyVoice2 流式语音 |
| `MOONSHOT_API_KEY` | Moonshot / Kimi |
| `MIAOYI_MOCK_LLM` | `1` 强制离线演示、`0` 强制关闭；留空为自动（非生产且无 Key 时启用） |

Key 缺失的供应商会**自动跳过**，不需要把清单配齐。

### 安全

| 变量 | 默认值 | 作用 |
|---|---|---|
| `JWT_SECRET_KEY` | 占位值 | **必填**，签发 access / refresh token 的密钥 |
| `INVITE_CODE` | 空 | 配置后注册必须填对邀请码，留空表示开放注册 |
| `REQUEST_SIGNING_ENABLED` | `false` | 开启 `x-signature` / `x-timestamp` / `x-nonce` 请求签名 |
| `REQUEST_TIMESTAMP_TOLERANCE` | `300` | 签名请求的时间窗（秒） |
| `CLEAR_NON_ADMIN_ON_STARTUP` | `false` | 开发期开关；生产务必保持 false |

### 数据库（仅 MySQL 需要）

`MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_USER`、`MYSQL_PASSWORD`、`MYSQL_DATABASE`。
在 `config.yaml` 里改 `database.type: mysql`（或设 `MIAOYI_DB_TYPE=mysql`），再执行 `python migrations/sqlite_to_mysql.py` 迁移历史数据。

### 可选记忆微服务

`MEMORY_TOKEN` —— 当 `memory.remote.enabled: true` 时，`server.py` 与 `memory_server.py` 之间的共享令牌。

---

## 三、config.yaml 详解

### 3.1 大模型供应商池

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

- `provider` 取值：`zhipu`（原生 SDK）或 `openai-compat`（任何 OpenAI 兼容端点：DeepSeek、百炼、Moonshot、硅基流动、vLLM…）
- `openai-compat` 必填 `api_base`
- **顺序即优先级**：从上到下尝试；某家出现 429 / 5xx / 超时会进入 **60 秒冷却**，请求自动切到下一家
- `min_max_tokens`（可选）为推理型模型抬升 token 预算——这类模型会先花 token 生成 `reasoning_content`，预算过小时正文为空

**新增供应商**只有两步——追加条目、导出 Key：

```yaml
    - provider: openai-compat
      api_key_env: MY_VENDOR_KEY
      api_base: https://api.example.com/v1
      model_name: my-model
```

### 3.2 角色路由

每个内部「脑区」按角色向池子要模型：

| 角色 | 使用位置 | 建议模型 |
|---|---|---|
| `chat` | 主回复流式输出 | 你能负担的最强模型 |
| `light` | 意图分类、寒暄降级 | 便宜模型 |
| `inner` | 内心独白（先想后说） | 便宜模型 |
| `think` | 深度推理步骤 | 视预算而定 |
| `emotion` | 情感洞察 + 看图感受 | 便宜模型 |
| `tool` | 工具决策（天气 / 时间 / 搜索） | 便宜模型 |
| `memory` | 记忆质量门（预留） | 便宜模型 |
| `proactive` | 主动消息起草（预留） | 便宜模型 |

```yaml
  roles:
    chat:      {model: deepseek-flash}
    light:     {model: glm-4-flash}
    # …
```

两条容易踩的规则：

- 角色里的 `model` 只有在 `providers` 中**存在同名 `model_name`** 且该家配了 Key 时才生效，否则回退主供应商
- 调用方显式传 `model` 时会绕过角色路由；故障转移使用各家自己的 `model_name`。刻意不把厂商专有型号名塞进 fallback，否则会触发 400 连环报错。

### 3.3 视觉与向量

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

- 没有视觉 Key 时降级为「我看不了图」，而不是整条消息失败
- 没有向量 Key 时记忆回退关键词检索，聊天照常

### 3.4 语音

```yaml
voice:
  enabled: true
  asr_engine: whisper          # faster-whisper
  whisper_model: base
  tts_engine: edge             # edge | gptsovits | cosyvoice
  cosyvoice_enabled: true      # 需要 DASHSCOPE_API_KEY
  gptsovits_url: ''            # 例如 http://127.0.0.1:9880
  wake_word_enabled: false
```

TTS 降级链为 **CosyVoice2 → GPT-SoVITS → Edge TTS → 百度 → 本地 → gTTS**，不可用或合成出空音频的层级会被自动跳过。无声卡的服务器请设 `local_tts_enabled: false`。

### 3.5 记忆

| 键 | 默认 | 含义 |
|---|---|---|
| `max_history` | 50 | 进入 prompt 的历史轮数 |
| `memory_retention_days` | 90 | 记忆记录的软保留期 |
| `vector_top_k` | 3 | 每轮召回的条数 |
| `vector_threshold` | 0.7 | 相似度下限 |
| `cache_size` | 50 | 进程内缓存条数 |
| `remote.enabled` | false | 记忆是否走 `memory_server.py`（HTTP） |

`remote.enabled: true` 时先启动 `python memory_server.py`，并保证两个进程的 `MEMORY_TOKEN` 一致；`false` 时记忆在主进程内，最省事。

### 3.6 主动关怀

| 键 | 默认 | 含义 |
|---|---|---|
| `silence_timeout_seconds` | 900 | 沉默多久后才考虑主动开口 |
| `trigger_probability` | 0.3 | 每个评估窗口的触发概率 |
| `noise_budget_per_hour` | 2 | 每小时主动消息硬上限 |
| `dnd_start` / `dnd_end` | 23 / 8 | 免打扰时段（跨午夜）；期间压下的意愿合并成一条早安消息 |
| `moments_enabled` | true | 模拟朋友圈动态 |
| `moments_interval_hours` | 3 | 动态间隔 |
| `moments_max_per_day` | 4 | 每日上限 |

`noise_budget_per_hour` 与免打扰时段是防止「陪伴变骚扰」的两个关键项。

### 3.7 其他配置段

| 配置段 | 关键项 |
|---|---|
| `server` | `host`、`port`（默认 8001）、`reload`、`max_port_try` |
| `database` | `type`、`mysql.{host,port,user,password,database}` |
| `features` | `emotional_memory_enabled`、`proactive_chat_enabled`、`style_mimicry_enabled`、`voice_input_enabled`、`voice_output_enabled`、`deep_thinking_enabled`、`autobiography_enabled` |
| `emotion` | `emotional_independence`、`empathy_coefficient`、`negative_window_minutes`、`redirection_threshold` |
| `search` | `enabled`、`sources`、`max_results`、`timeout` |
| `security` | `max_message_length`（默认 2000）、`sensitive_word_enabled` |
| `performance` | `thread_pool_size`、`max_concurrent_thinking`、`thinking_timeout_seconds`、`cache_ttl_hours` |
| `appearance` | 网页端主题色 |
| `logging` | `level`、`max_bytes`、`backup_count` |

---

## 四、成本控制方案

1. **最省钱的可用配置** —— 一个免费 Key（智谱 `glm-4-flash`）喂满所有角色，视觉与向量复用同一个 Key。
2. **性价比路线** —— 只给 `chat` 配强模型，其余七个角色用便宜模型，这也是示例配置的形态。
3. **零成本** —— Key 全留空走演示模式，或显式设 `MIAOYI_MOCK_LLM=1`。
4. **止血** —— 调低 `noise_budget_per_hour`、调高 `silence_timeout_seconds`、调小 `max_history`，并把 `llm.roles.inner` 换成最便宜的模型（内心独白触发频率很高）。

---

## 五、常见问题

| 现象 | 多半是 |
|---|---|
| 启动即退出并打印安全提示 | `JWT_SECRET_KEY` 缺失或仍是占位值 |
| 日志显示登录成功，App 却毫无反应（安卓） | CORS：`ALLOWED_ORIGINS` 缺 `http://localhost`、`https://localhost`、`capacitor://localhost` |
| 回复像固定示例句 | 没找到可用 Key → 演示模式。检查 `ZHIPU_API_KEY`，或设 `MIAOYI_MOCK_LLM=0` |
| 某家持续 429 | 该供应商在冷却；往池子里加第二家 |
| 通话接通但没有声音 | AudioContext 未在用户手势内 resume，或所有 TTS 层级都不可用 |
| 记忆召回很随机 | 向量 Key 缺失（回退关键词），或 `vector_threshold` 设得太低 |
| 8001 端口悄悄变了 | 旧进程还占着端口，重启前先杀干净 |
