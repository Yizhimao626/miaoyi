# 架构说明 / Architecture

本文档说明喵艺的核心分层与关键链路，方便快速定位代码。

## 1. 分层

```
客户端 (React / Capacitor Android)
        │  REST + WebSocket
        ▼
API 层 (server.py)          ── 鉴权、会话、记忆、通话、管理后台路由
        │
        ▼
引擎层 (core/)              ── 对话、情感、记忆、人格、LLM 路由、语音、主动关怀
        │
        ▼
服务层 (services/、utils/)  ── 消息编排、日志、配置、会话状态
        │
        ▼
数据层 (db_manager.py)      ── SQLite / MySQL 抽象，统一读写入口
```

设计原则：**API 层只做协议转换与鉴权，业务判断全部下沉到 `core/`**，这样同一套引擎既服务 HTTP 也服务 WebSocket，未来接第三方协议时不需要复制逻辑。

## 2. 一次对话的请求链路

1. 客户端通过 WebSocket 发送用户消息（HTTP 接口同样支持，供无 WS 场景降级）
2. 对话引擎组装上下文：系统人格 + 情感状态 + 检索到的记忆 + 最近若干轮对话
3. **LLM 路由**按角色选择模型：
   - `emotion`：判断用户情绪，输出结构化 JSON（快模型）
   - `light`：意图分类（是否需要搜索、是否工具调用）
   - `chat`：生成用户实际读到的回复（推理模型，流式）
4. 流式 token 逐块推回客户端
5. 对话结束后异步更新情感状态、写入记忆、必要时触发主动关怀

## 3. LLM 供应商池

- 供应商在 `config.yaml` 的 `llm.providers` 中声明，`api_key_env` 指向环境变量名
- 未配置 key 的供应商在启动时自动跳过
- 某个供应商连续失败后进入 **60 秒冷却**，期间请求转向下一个可用供应商
- 调用方显式指定 `model` 时，该指定只对原始首供应商生效，回退时使用各供应商自己的默认模型（避免不同厂商模型名混用导致 400）

## 4. 情感与记忆

- **情感状态**：心情、亲密度、成长等级按用户持久化，注入 system prompt 影响语气与长度
- **记忆**：每条记忆带情感标签与时间戳；检索时同时考虑语义相似度与情感相关度
- **记忆卡片**：检索结果会作为可视化卡片返回前端，用户可以感知"它记得我"

## 5. 通话链路

```
客户端点击拨号
  → POST /call/dial（建立通话会话，返回 call_id）
  → WebSocket 建立连接（必须携带有效 token，且与路径 user_id 一致）
  → 客户端录音并分片上传
  → 后端 ASR（faster-whisper）转写
  → 对话引擎生成回复
  → TTS 合成 → 二进制音频帧推回
  → 客户端 AudioContext 解码播放
```

两个已知的工程细节：

- **移动端自动播放限制**：AudioContext 必须在用户手势内 `resume()`，否则表现为"接通了但没声音"
- **TTS 降级链**：CosyVoice2 → GPT-SoVITS → Edge TTS → 百度 TTS → 本地 → gTTS；任一环节不可用自动降级，空音频不会推给前端

## 6. 数据模型（简化）

| 表 / 文件 | 用途 |
|---|---|
| `users` | 账号、鉴权、角色 |
| `sessions` / `messages` | 会话与消息历史 |
| `memories` | 记忆条目（文本、情感标签、向量、时间） |
| `emotion_state` | 心情、亲密度、成长值 |
| `data/*.json` | 轻量运行时状态（缓存、设置、用户偏好） |

## 7. 部署形态

- 单机部署：systemd 托管 `server.py`，FastAPI 直接监听端口（无 nginx），前端静态产物由后端托管
- 容器部署：`docker compose up -d`，数据、备份、日志各挂一个 volume
- 安卓：Capacitor 打包 WebView 壳，APK 通过后端 `/download/latest` 分发，应用内检查 `/api/version/check`
