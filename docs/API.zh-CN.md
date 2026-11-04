# 接口文档

[English](API.md) · **简体中文**

开发环境地址：`http://localhost:8001`。生产部署由 FastAPI 直接对外提供同样的路径，不需要反向代理。

> 交互式 OpenAPI 文档在 `/docs` 与 `/openapi.json`，**仅非生产环境可用**（`ENVIRONMENT=production` 时两者关闭）。

---

## 一、通用约定

### 响应信封

所有 REST 响应统一为：

```json
{ "code": 200, "data": { }, "message": "ok" }
```

`code` 与 HTTP 状态码一致。出错时 `data` 为 `null`，`message` 为中文提示（与产品人格一致）。

### 鉴权

```http
Authorization: Bearer <access_token>
```

浏览器端另有 Cookie 兜底（`miaoyi_token`，登录时写入）；WebSocket 用查询参数传 token。

### 限流

| 接口 | 限制 |
|---|---|
| `POST /api/auth/register` | 5 次 / 分钟 |
| `POST /api/auth/login` | 10 次 / 分钟 |
| `POST /api/auth/refresh` | 20 次 / 分钟 |
| `POST /api/message` | 30 次 / 分钟 |
| `POST /api/message/stream` | 20 次 / 分钟 |
| `GET /api/message/history` | 60 次 / 分钟 |
| `GET /api/search` | 15 次 / 分钟 |
| `POST /api/speak`、`/api/voice/synthesize` | 20 次 / 分钟 |
| `GET /api/version/check`、`/download/latest` | 10 次 / 分钟 |

免费账号另有**每日 50 条**对话上限（后端进程内计数）。

---

## 二、鉴权

### `GET /api/auth/config`
公开。告诉客户端注册是否需要邀请码。

```json
{ "code": 200, "data": { "invite_code_required": false }, "message": "ok" }
```

### `POST /api/auth/register`
公开。请求体：

```json
{ "username": "cxtest1021x", "phone": "13800000000", "password": "Test123456", "email": "", "invite_code": "" }
```

- `username` 3–50 字符；`phone` 须符合 `1[3-9]\d{9}`；密码强度会校验
- 返回 `201`，含 `user_id`、`username`、`token`、`access_token`、`refresh_token`

### `POST /api/auth/login`
公开。`username` 可以是用户名**或**大陆手机号。返回与注册相同的 token 字段，同时写入 `miaoyi_token` Cookie。

- 连续失败会锁定账号 → `429`
- 账号被禁用 → `403`

```bash
curl -X POST http://localhost:8001/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"cxtest1021x","password":"Test123456"}'
```

### `POST /api/auth/refresh`
公开。请求体 `{"refresh_token": "..."}`（也可放在 `Authorization` 头里）。

### `POST /api/auth/logout`
需鉴权。撤销当前用户的 refresh token。

---

## 三、聊天

### `POST /api/message`
最简单的一次性路径。

```json
{ "text": "在吗", "context": "" }
```

返回 `data`：

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

用户消息与回复都由后端持久化，客户端不需要自己写历史。

### `POST /api/message/stream`
请求体同上，可加 `client_msg_id`（窗口内重复返回 `409`）。响应为 **SSE**：

```
data: {"type":"typing","status":"typing"}
data: {"type":"thinking_status","content":"🤔 正在分析情感...","status":"thinking"}
data: {"type":"...","text":"..."}
data: {"type":"typing","status":"done"}
```

### 其他聊天接口

| 方法 | 路径 | 作用 |
|---|---|---|
| `GET` | `/api/message/history?limit=20&before_message_id=` | 分页历史（`messages`、`has_more`、`count`） |
| `DELETE` | `/api/message/{message_id}` | 删除单条消息 |
| `POST` | `/api/message/regenerate` | 重新生成上一条回复 |
| `GET` | `/api/conversations` | 最近会话（最多 20 条） |
| `DELETE` | `/api/conversation/{session_id}` | 删除单个会话 |
| `POST` | `/api/conversation/clear` | 清空全部会话 |
| `GET` / `POST` | `/api/session/list`、`/api/session/create` | 会话列表 / 新建（`{"name": "..."}`） |
| `DELETE` | `/api/session/{session_id}` | 删除会话 |
| `POST` | `/api/export/chat` | 导出聊天记录 |
| `GET` | `/api/commands/list` | 斜杠命令清单（`/help`、`/status`、`/emotion`…） |

---

## 四、WebSocket `/ws`

```
ws://localhost:8001/ws?token=<access_token>
```

- token 必须是**有效的 access token**；Cookie `miaoyi_token` 可兜底
- 关闭码 `4001` = 鉴权失败，`4002` = 同账号并发连接超过 10 条

### 服务端 → 客户端

| `type` | 时机 | 说明 |
|---|---|---|
| `greeting` | 连接建立 | 启动问候语 |
| `emotional_state` | 连接时、对话后 | 当前四维情感状态 |
| `growth_data` | 连接时 | `level`、`exp`、`exp_max`、`intimacy`、`stage` |
| `typing` | 回复前 | `status: "typing"` / `"done"` |
| `thinking`、`thinking_status`、`thinking_step` | 处理中 | 思考过程，供界面展示 |
| `response_chunk` | 流式输出中 | 增量文本 |
| `response` | 本轮结束 | 完整载荷（`text`、`sticker`、`thought`、`emotional_state`、`audio_url`、`client_message_id`…） |
| `thought_bubble` | 偶尔 | 内心独白气泡 |
| `speech` | ASR 之后 | 语音输入识别出的文字 |
| `proactive` | 主动触发 | 主动关怀消息 |
| `tts_result` | 回应 `tts` 帧 | `audio_url` 或 `error` |
| `pong` | 回应 `ping` | 心跳 |
| `error` | 失败时 | `code` + `message` |

### 客户端 → 服务端

| `type` | 字段 | 作用 |
|---|---|---|
| `message` | `text`、`context`、`reply_to`、`client_message_id`、`image_url` | 发送聊天消息（`image_url` 必须是本站 `/uploads-chat/...` 路径） |
| `ping` | — | 心跳，服务端回 `pong` |
| `tts` | `text` | 合成语音，上限 12 次/分钟 |
| `voice_text` | `text` | 通话中的 ASR 文本，触发通话回复 |
| `barge_in` | — | 用户打断播报，作废进行中的回合 |
| `thinking_status` | `content` | 客户端状态回显 |

二进制帧为通话 ASR 的原始音频（WebM，单帧 ≤10 MB）。

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

## 五、情感、关系与成长

| 方法 | 路径 | 返回 |
|---|---|---|
| `GET` | `/api/emotion/state` | 当前 `joy` / `arousal` / `intimacy` / `trust` 与主导情绪 |
| `GET` | `/api/emotion/history` | 近期情绪轨迹 |
| `GET` | `/api/emotional_state` | 界面使用的综合情感快照 |
| `GET` | `/api/relationship` | `intimacy` 与关系阶段名 |
| `GET` | `/api/relationship/status` | 完整关系摘要 |
| `GET` | `/api/relationship/intimacy`、`/api/relationship/anniversary` | 亲密度数值 / 纪念日信息 |
| `GET` | `/api/growth`、`/api/growth/data` | `level`、`exp`、`exp_max`、`title`、`achievements`、`intimacy` |
| `GET` | `/api/personality`、`POST /api/personality` | 读取 / 修改人格设置 |
| `POST` | `/api/learn` | 投喂样本用于风格模仿 |
| `GET` | `/api/character-card/export` | 导出角色卡 |

---

## 六、记忆

| 方法 | 路径 | 作用 |
|---|---|---|
| `GET` | `/api/memory/list?limit=50` | 记忆记录（`key`、`value`、`created_ts`），1–500 |
| `POST` | `/api/memory/save` | 手动保存一条记忆 |
| `DELETE` | `/api/memory/{key}` | 按 id 或 key 删除 |
| `PATCH` | `/api/memory/{key}` | 编辑记忆 |
| `POST` | `/api/memory/{key}/pin` | 置顶 / 取消置顶 |
| `GET` | `/api/memory/search` | 记忆语义检索 |
| `GET` | `/api/memory/tags`、`GET /api/memory/tag/{tag}`、`POST /api/memory/tag`、`DELETE /api/memory/tag` | 标签管理 |
| `POST` | `/api/memory/clear` | `{"days": null}` 清空全部；传数字则清理 N 天前的记录 |
| `GET` | `/api/memory_triggers` | 近期被触发的记忆 |

---

## 七、语音

| 方法 | 路径 | 作用 |
|---|---|---|
| `GET` | `/api/voice_status` | 语音子系统可用性 |
| `GET` / `POST` | `/api/voice-mode` | 读取 / 设置语音模式（`{"mode": 1}`） |
| `POST` | `/api/speak` | `{"text": "..."}` → `{"audio_url": "/voice_cache/xxx.mp3"}` |
| `POST` | `/api/voice/synthesize` | 与 `/api/speak` 等价 |
| `GET` | `/api/voice/segment?task_id=&index=` | 取流式回复的第 N 段语音 |

所有 TTS 接口返回 URL 而不是二进制，音频文件走静态服务。

---

## 八、媒体与文件

| 方法 | 路径 | 作用 |
|---|---|---|
| `POST` | `/api/upload/file` | 表单上传。图片 ≤10 MB（jpg/jpeg/png/webp/gif），文档 ≤20 MB（pdf/doc/docx/xls/xlsx/ppt/pptx/zip/rar/txt）。图片返回 `/uploads-chat/...` 地址 |
| `GET` | `/api/stickers` | 表情包清单 |
| `GET` | `/api/emoticons/search`、`POST /api/emoticons/create`、`POST /api/sticker/upload` | 表情搜索 / 生成 |
| `GET` | `/api/files/exports/{filename}`、`/api/files/uploads/{filename}` | 下载导出文件与上传文件 |

---

## 九、系统与其他

| 方法 | 路径 | 作用 |
|---|---|---|
| `GET` | `/api/version/check?platform=android&version_code=10` | 检查更新：`has_update`、`force_update`、`download_url`、`file_size` |
| `GET` / `HEAD` | `/download/latest` | 最新 APK（匿名可下，按 IP 限流） |
| `GET` | `/api/user/profile`、`DELETE /api/user/profile` | 读取 / 注销账号 |
| `PUT` | `/api/user/password`、`PUT /api/user/nickname` | 改密码 / 改昵称 |
| `POST` | `/api/user/checkin` | 每日签到（提升亲密度） |
| `GET` | `/api/onboarding`、`/api/daily_fragment` | 首次引导、每日碎片 |
| `GET` | `/api/config`、`POST /api/config` | 客户端可见的应用设置 |
| `GET` | `/api/usage` | 用量统计 |
| `GET` | `/api/metrics` | 进程内指标（延迟分桶、模型分布） |
| `GET` | `/api/search?query=` | 网络 / 知识搜索（15 次/分钟） |
| `POST` | `/api/feedback` | 用户反馈 |
| `GET` | `/api/diary/today`、`/api/diary/list`、`/api/diary/memory-lane` | 日记视图 |
| `GET` | `/api/moments/list`、`POST /api/moments/{id}/like` | 模拟朋友圈 |
| `GET` | `/api/plugins/list`、`POST /api/plugins/{id}/toggle` | 插件开关 |
| `GET` | `/api/holiday`、`/api/time` | 节假日查询、服务器时间 |
| `GET` | `/api/thinking_log`、`/api/autobiography` | 思考记录、自传片段 |
| `GET` | `/api/push/vapid-key`、`POST /api/push/{subscribe,unsubscribe,notify}` | Web Push |
| `GET` | `/api/admin/llm-pool`、`/api/admin/user/{id}` | 仅管理员：池健康度、用户排查 |

---

## 十、状态码

| 码 | 含义 |
|---|---|
| `200` | 成功 |
| `201` | 账号创建成功 |
| `400` | 参数校验失败（空消息、手机号格式、密码强度…） |
| `401` | token 缺失 / 无效 / 过期，或凭据错误 |
| `403` | 账号被禁用、邀请码错误、缺少签名 |
| `404` | 会话、记忆或文件不存在 |
| `409` | `client_msg_id` 重复 |
| `500` | 服务端异常（返回文案刻意模糊，避免泄露细节） |

客户端约定：`401` 视为「刷新一次 token，仍失败则登出」；`403` / `429` 的 `message` 要原样展示——里面才是真实原因（锁定、禁用、额度）。
