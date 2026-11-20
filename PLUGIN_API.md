# 接口契约：Harness 插件、Copilot Provider、事件协议、运行时配置

本文是 RealTime Copilot 与 DeepSeek Harness 之间、以及 Copilot 内部各进程之间的**可执行契约**。
实现与本文不一致时，以本文为准并修正实现；确实需要变更契约时，先改本文并升版本号。

协议版本：`v1`（本文所有 `"v": 1`）。

---

## 1. Harness 插件契约

### 1.1 包清单

```jsonc
// package.json（npm 包名不占用 @deepseek-ai scope）
{
  "name": "@realtimecopilot/dsh-copilot-bridge",
  "version": "0.1.0",
  "type": "module",
  "main": "lib/index.js",
  "types": "lib/types/index.d.ts",
  "exports": {
    ".": { "types": "./lib/types/index.d.ts", "default": "./lib/index.js" },
    "./cordis.patch.yml": "./cordis.patch.yml",
    "./package.json": "./package.json"
  },
  "files": ["lib/index.js", "cordis.patch.yml", "lib/types/**/*.d.ts"],
  "dsh": {
    "bundle": { "patch": "./cordis.patch.yml" }
  },
  "peerDependencies": {
    "@deepseek-ai/cordis": "^0.2.1",
    "@deepseek-ai/dsh-tools": "^0.2.1",
    "@deepseek-ai/dsh-agent": "^0.2.1"
  },
  "dependencies": { "ws": "^8.21.3" }
}
```

约束（来自 `docs/cookbook/adding-a-package.md` 与 Harness 的依赖规则）：

- `@deepseek-ai/dsh-*` 只能出现在 `peerDependencies`（与宿主同一份实例），绝不进 `dependencies`——否则 pnpm 会装出第二份模块单例。
- `@deepseek-ai/cordis` 同时出现在 `peerDependencies` 与 `devDependencies`（同范围）。
- 精确锁定 peer 版本，不做 `workspace:*`（我们不在该 monorepo 内）。
- **不导出** `./client`：不绑定 Harness 的 React 版本。

### 1.2 bundle patch

```yaml
# cordis.patch.yml —— 相对空 profile 根的一次 insert
- insert:
    - id: copilot-bridge
      name: '@realtimecopilot/dsh-copilot-bridge'
      config:
        endpoint: 'auto'            # 或显式 ws://127.0.0.1:PORT
        injectContext: false        # 默认关闭：注入会写入会话日志
        injectMode: 'question'      # question | transcript
        tools: true
```

`id` 是 patch 的定位键，用户可在自己的 `cordis.patch.yml` 里按该 id 覆盖 config 或 `disabled: true`。

### 1.3 插件模块

```ts
import type { Context } from '@deepseek-ai/cordis'
import z from '@deepseek-ai/schemastery'
import { defineTool } from '@deepseek-ai/dsh-tools'

export const name = 'copilot-bridge'
export const inject = ['tools']          // 需要工具注册表；上下文注入另按需注入 agents

export interface Config {
  endpoint: 'auto' | string
  injectContext: boolean
  injectMode: 'question' | 'transcript'
  tools: boolean
}
export const Config: z<Config> = z.object({
  endpoint: z.union([z.const('auto'), z.string()]).default('auto'),
  injectContext: z.boolean().default(false),
  injectMode: z.union([z.const('question'), z.const('transcript')]).default('question'),
  tools: z.boolean().default(true),
})

export function apply(ctx: Context, config: Config): void {
  // 1) 读 runtime.json → 连 Core（失败不抛：Harness 必须能正常启动）
  // 2) ctx.effect(...) 注册 3 个只读工具
  // 3) ctx.inject(['agents'], (agentCtx) => agentCtx.on('agent/pre-step', ...)) 条件注入
  // 4) ctx.provide('copilot', service)  ← 服务名见 §1.5
}
```

**失败策略**：Core 未运行不是错误。插件必须能加载、工具必须存在、调用时返回结构化错误（`copilot_unavailable`）。绝不因为我们的服务不可用而让 dsh 启动失败。

### 1.4 工具

三个只读工具，`parameters` 为 JSON Schema：

| 工具 | 参数 | 返回 |
|---|---|---|
| `copilot_transcript` | `{ minutes?: number }`（1–120，默认 10） | `{ segments: [{ source, text, startMs, endMs, confidence }], truncated: boolean }` |
| `copilot_last_answer` | `{}` | `{ question?: string, answer?: string, firstTokenMs?: number, createdAt?: number }` 或 `{ empty: true }` |
| `copilot_ask` | `{ question: string }`（1–500 字符） | `{ answer: string, model: string, firstTokenMs: number }` |

工具描述必须写明：这是**会议内容**，可能包含敏感信息；`copilot_ask` 会调用外部模型并产生费用。

错误码（工具返回的结构化错误，不抛异常）：

| code | 含义 |
|---|---|
| `copilot_unavailable` | 未连接 Core |
| `copilot_no_session` | Core 在线但没有进行中的会议会话 |
| `copilot_timeout` | 等待答案超过 `answerTimeoutMs` |
| `copilot_invalid_request` | 参数越界（工具层已校验，出现即 bug） |

### 1.5 服务定义

```ts
/** 供其他插件/客户端读取的只读状态；写入一律走 Core 的 HTTP/WS。 */
export interface CopilotService {
  status(): {
    connected: boolean
    coreVersion?: string
    captureState: 'idle' | 'capturing' | 'error'
    lastEventAt?: number
  }
  latestAnswer(): { question: string; answer: string; createdAt: number } | undefined
  transcript(minutes: number): Promise<TranscriptSegment[]>
  ask(question: string, signal?: AbortSignal): Promise<AnswerRecord>
}
```

`ctx.copilot` 在插件卸载时必须被释放（Cordis 的 `provide` 随 effect 回收）。

### 1.6 上下文注入（默认关闭）

在 `agent/pre-step` 返回的 `messages` 末尾追加一条 user message：

```ts
createUserMessage({
  content: [{ type: 'text', text: rendered }],
  source: {
    kind: 'realtime-copilot',
    form: 'snapshot',
    sections: [{ name: 'realtime-copilot', text: rendered }],
  },
})
```

`rendered` 的模板（版本 `ctx-v1`，变更需升版本并记录）：

```markdown
[会议实时上下文 v1 · 采集中 · 来源：远端]
最近问题："<question text>"（置信度 0.82，3 秒前）

最近转写（按时间顺序，最多 20 条）：
- [远端 14:02:31] ...
- [远端 14:02:36] ...

当前悬浮窗答案（若已生成）：
"<answer text>"
```

规则：

- 只在 `injectContext: true` 且存在进行中会话时注入；无内容时不注入空消息。
- 注入内容会进入 Harness 会话日志并可能被上传（Harness 规则：模型可见 ⟺ 已记录），设置页必须显式告知。
- 每条消息带 `source.kind = 'realtime-copilot'`，便于用户与工具区分来源。
- 注入频率受 Core 的 `contextInjection.minIntervalMs`（默认 15000）限制，避免污染缓存前缀。

---

## 2. 事件协议（Core ↔ UI / bridge）

### 2.1 传输

- WebSocket，`ws://127.0.0.1:<port>/ws/v1/events`，仅监听 `127.0.0.1`。
- 文本帧 = JSON 事件；二进制帧 = 音频（仅 `role=audio` 的 sidecar 连接允许）。
- 心跳：服务端每 15s 发 `{"type":"ping"}`，客户端回 `{"type":"pong"}`；两个周期无活动则断开。
- 背压：单连接发送队列上限 1 MiB，超出则丢弃 `transcript.partial` 与 `capture.level`（可丢事件），**不丢** `question.*` / `answer.*` / `capture.state`；丢弃计数随下一帧 `dropped` 字段上报。

### 2.2 信封

```jsonc
{
  "v": 1,
  "type": "answer.delta",
  "id": "evt_01J...",          // ULID，单调
  "seq": 4213,                  // 每连接单调递增，从 1 开始
  "ts": 1763700000123,          // 事件产生时刻（Unix ms）
  "sessionId": "mtg_01J...",    // 会议会话；系统事件为 null
  "payload": { }
}
```

### 2.3 握手

客户端连接后 **2 秒内**必须发送：

```jsonc
{ "v": 1, "type": "hello", "token": "<runtime token>", "role": "ui|bridge|audio|tests",
  "clientVersion": "0.1.0" }
```

服务端成功：

```jsonc
{ "v": 1, "type": "system.ready", "seq": 1, "ts": 0,
  "payload": { "serverVersion": "0.1.0", "protocol": 1,
               "capabilities": ["transcript", "question", "answer", "kb"],
               "sessionId": null } }
```

失败：`system.error { code: "auth_failed" | "protocol_version_unsupported" }` 后立即关闭连接。

`role=audio` 的连接只能发二进制音频与控制帧，不能订阅事件。

### 2.4 事件类型（v1 全集）

| type | 方向 | payload | 可丢 |
|---|---|---|---|
| `system.ready` | S→C | 见上 | 否 |
| `system.error` | S→C | `{ code, message, retryable }` | 否 |
| `capture.state` | S→C | `{ state: 'idle'\|'starting'\|'capturing'\|'stopping'\|'error', source: 'loopback'\|'mic', deviceId?, since, error? }` | 否 |
| `capture.level` | S→C | `{ rmsDbfs, peakDbfs }` | 是 |
| `capture.silent` | S→C | `{ seconds, hint }` | 否 |
| `asr.status` | S→C | `{ state: 'connecting'\|'ready'\|'degraded'\|'error', provider, error? }` | 否 |
| `transcript.partial` | S→C | `{ segmentId, source, text }` | 是 |
| `transcript.segment` | S→C | `{ segmentId, source, text, startMs, endMs, confidence }` | 否 |
| `question.detected` | S→C | `{ questionId, segmentId, text, confidence, detector, anchorTs }` | 否 |
| `question.dismissed` | S→C | `{ questionId, reason: 'superseded'\|'low_confidence'\|'manual' }` | 否 |
| `answer.start` | S→C | `{ answerId, questionId, model }` | 否 |
| `answer.delta` | S→C | `{ answerId, text }` | 是（合并） |
| `answer.end` | S→C | `{ answerId, status: 'complete'\|'cancelled'\|'error', firstTokenMs, totalMs, usage? }` | 否 |
| `answer.error` | S→C | `{ answerId, code, message, retryable }` | 否 |
| `kb.status` | S→C | `{ enabled, documents, lastIndexedAt }` | 否 |
| `latency.sample` | S→C | `{ stage, anchorTs, firstTokenTs?, totalTs? }` | 是 |

客户端→服务端（除 `hello`）：

| type | payload | 说明 |
|---|---|---|
| `capture.start` | `{ loopback: { deviceId?: string, processId?: number }, mic: { enabled: boolean, deviceId?: string } }` | 必须来自用户手势 |
| `capture.stop` | `{}` | |
| `copilot.ask` | `{ question: string, requestId }` | bridge 的 `copilot_ask` 用它 |
| `transcript.query` | `{ minutes, requestId }` | 返回 `transcript.result` |
| `settings.update` | `{ patch: Partial<Config> }` | 仅 `role=ui` |
| `pong` | `{}` | 心跳 |

### 2.5 音频帧（`role=audio`）

音频走独立的二进制协议，完整定义见 [`packages/protocol/audio-frame.v1.md`](packages/protocol/audio-frame.v1.md)。这里只记录与事件协议的边界：

- 一条 WebSocket Binary Message 恰好一帧，28 字节小端头 + 640 字节 PCM16LE（共 668 字节）；
- 头部包含 `protocolVersion`、`source`、`frameDurationMs`、`sequence`、`captureTsNs`、`sampleRate`、`channels`、`sampleFormat`、`payloadLength`；
- 只有 `role=audio` 的连接可以发送；其他角色发送会被 `invalid_role` 拒绝并断开；
- 帧级错误只丢弃并计数（`/healthz` 的 `audio` 字段），持续违规才断开；
- PCM 不进 JSON、不进日志、不落盘。

控制帧（文本）：`audio.ready { device, sampleRate, frameMs }`、`audio.error { code, message }`、`audio.device-changed { deviceId }`。

---

## 3. Provider 接口（Python，Core 内部）

所有 Provider 通过 `entry point` 或配置项 `provider` 选择，构造后只依赖传入的配置，不做全局状态。异步接口，取消除特定说明外统一用 `asyncio.CancelledError`。

### 3.1 通用类型

```python
Source = Literal["loopback", "mic"]

@dataclass(frozen=True)
class AudioFrameBatch:
    session_id: str
    source: Source
    pcm: bytes            # 16k mono PCM16LE
    start_ts_ns: int
    end_ts_ns: int

@dataclass(frozen=True)
class TranscriptSegment:
    segment_id: str
    session_id: str
    source: Source
    text: str
    start_ms: int
    end_ms: int
    confidence: float
    asr_provider: str
    is_final: bool
```

### 3.2 VadProvider

```python
class VadProvider(Protocol):
    name: str
    def push(self, batch: AudioFrameBatch) -> VadDecision: ...
    def reset(self) -> None: ...

@dataclass(frozen=True)
class VadDecision:
    state: Literal["silence", "speech_start", "speech", "speech_end"]
    segment_id: str | None      # speech_start 时分配
    anchor_ts_ns: int | None    # speech_end 时为最后一帧有效音频的时间戳
```

约束：`speech_end` 的 `anchor_ts_ns` 必须是**最后一帧语音**的时间戳，而不是判定时刻；这是延迟锚点的定义（见 ARCHITECTURE §4.2）。

### 3.3 AsrProvider

```python
class AsrProvider(Protocol):
    name: str
    mode: Literal["streaming", "utterance"]

    async def start(self, cfg: AsrConfig) -> None: ...
    async def feed(self, batch: AudioFrameBatch) -> None: ...
    async def end_segment(self, segment_id: str, anchor_ts_ns: int) -> None: ...
    def results(self) -> AsyncIterator[AsrResult]: ...
    async def stop(self) -> None: ...

@dataclass(frozen=True)
class AsrResult:
    segment_id: str
    text: str
    is_final: bool
    confidence: float
    emitted_ts_ns: int
    error: str | None = None
```

约束：

- `mode="streaming"`：`feed` 持续送入，`results()` 产 partial 与 final；`end_segment` 表示强制定稿。
- `mode="utterance"`：只在 `end_segment` 后产 final，`feed` 用于缓冲。
- `is_final=True` 之后，同一 `segment_id` 不得再产结果。
- 断流由 Provider 自己重连（上限与退避在配置里）；重连失败时产一条 `error` 结果，Core 转 `asr.status=error`。

### 3.4 QuestionDetector

```python
class QuestionDetector(Protocol):
    name: str
    async def detect(
        self, segment: TranscriptSegment, window: Sequence[TranscriptSegment]
    ) -> Detection | None: ...

@dataclass(frozen=True)
class Detection:
    is_question: bool
    confidence: float          # 0..1
    reasons: tuple[str, ...]   # 可解释：命中词、句型、模型标签
    normalized_text: str       # 去掉口头语后的问句
```

约束：必须是**纯判定**，不发副作用；同一 segment 重复调用结果稳定（可缓存）；`confidence < 0.45` 一律返回 `None`。

### 3.5 LlmProvider

```python
class LlmProvider(Protocol):
    name: str
    async def stream(self, req: AnswerRequest, signal: asyncio.Event) -> AsyncIterator[AnswerChunk]: ...

@dataclass(frozen=True)
class AnswerRequest:
    question: str
    system: str
    context: str
    model: str
    max_tokens: int
    temperature: float
    timeout_ms: int

# chunk: {"kind":"delta","text":...} | {"kind":"usage","prompt":int,"completion":int}
#      | {"kind":"done","finish":"stop"|"length"|"error","error":str|None}
```

约束：

- 首个 `delta` 到达时由 Core 记录 `first_token_ts`；Provider 不得缓存首个 delta。
- 必须支持取消：取消后不得再 yield；底层 HTTP 连接必须关闭。
- 失败抛 `LlmError(code, message, retryable)`，由 Core 决定是否重试（首答默认不重试）。

### 3.6 KnowledgeProvider

```python
class KnowledgeProvider(Protocol):
    name: str
    async def search(self, query: str, limit: int, timeout_ms: int) -> list[KbHit]: ...

@dataclass(frozen=True)
class KbHit:
    chunk_id: str
    document_title: str
    text: str
    score: float
```

约束：必须在 `timeout_ms` 内返回（超时返回空列表并记日志）；不得因 KB 故障阻塞答案生成。

### 3.7 注册与选择

`config.toml` 中：

```toml
[asr]
provider = "cloud-a"          # 对应 entry point "copilot.asr.cloud_a"

[answer]
provider = "deepseek-direct"  # 或 "harness-llm"
```

未知 provider 在启动期报错退出（fail loud），不静默回退。`harness-llm` 只在 bridge 在线时可用，启动时若指定但未连接，进入 `asr.status` 之外的 `answer` 降级态（配置 `fallback = "deepseek-direct"` 可显式允许回退）。

---

## 4. 运行时配置

### 4.1 位置与优先级

| 位置 | 用途 |
|---|---|
| `%APPDATA%\RealTimeCopilot\config.toml` | Core 主配置 |
| 环境变量 `RTC_*` | 覆盖（如 `RTC_SERVER_PORT`），仅用于诊断与测试 |
| UI 设置页 | 写入 config.toml 的 `[ui]` 段与部分 `[answer]` 字段 |

优先级：内置默认 < config.toml < 环境变量。所有字段启动时由 pydantic 校验，非法值退出并给出字段路径。

### 4.2 完整模式（默认值即出厂值）

```toml
[server]
host = "127.0.0.1"        # 固定，不可配置为其他值
port = 0                  # 0 = 由系统分配，写回 runtime.json
auth_timeout_ms = 2000

[mode]
mode = "standalone"       # standalone | harness

[capture]
frame_ms = 20
sample_rate = 16000
loopback_enabled = true
mic_enabled = false       # 默认关闭，显式开启
silence_report_seconds = 5

[vad]
provider = "energy"       # energy | silero
speech_threshold_dbfs = -38
min_speech_ms = 250
end_silence_ms = 280
max_segment_ms = 15000

[asr]
provider = "cloud-a"
language = "zh-CN"
interim = true
connect_timeout_ms = 3000
reconnect_attempts = 3

[detection]
heuristic_enabled = true
llm_review = false        # 开启会增加 ~100ms
dedupe_window_ms = 800
min_confidence = 0.45

[answer]
provider = "deepseek-direct"
fallback = ""             # 空 = 不降级
model = "deepseek-chat"
max_tokens = 240
temperature = 0.5
timeout_ms = 8000
system_prompt_version = "answer-v1"
debounce_ms = 300
max_concurrent = 2

[context]
max_segments = 20
max_chars = 4000
injection_min_interval_ms = 15000

[knowledge]
enabled = true
provider = "sqlite-fts"
limit = 5
timeout_ms = 120

[privacy]
persist_audio = false
debug_audio_retention_hours = 24
hide_from_screen_capture = true
inject_into_harness = false

[ui]
overlay_position = "bottom-right"
opacity = 0.92
click_through = true
hotkeys = { toggle = "Ctrl+Alt+R", stop = "Ctrl+Alt+S" }
```

### 4.3 Harness bridge 配置（cordis.patch.yml）

| 字段 | 默认 | 对应 Core 侧 |
|---|---|---|
| `endpoint` | `"auto"` | 读 `runtime.json` 的 `wsUrl` |
| `injectContext` | `false` | `privacy.inject_into_harness` |
| `injectMode` | `"question"` | 注入 `question` 或 `transcript` |
| `tools` | `true` | 是否注册三个工具 |

bridge **不**读取 `config.toml`；两边通过事件协议交互，配置各管各的。唯一的共享状态是 `runtime.json`（端口 + token）。

---

## 5. 版本与兼容

- **协议版本**：`v1`。破坏性变更（字段删除、语义改变、枚举收缩）必须升 `v=2`；旧客户端收到 `protocol_version_unsupported` 并提示升级。新增可选字段不升版本。
- **事件 `type` 是封闭集合**：客户端遇到未知 type 必须忽略并记录，不得崩溃。这是前向兼容的唯一要求。
- **插件版本**：bridge 的 `peerDependencies` 精确锁 `0.2.1`；Harness 升到 `0.3.x` 时先跑兼容矩阵（`TEST_PLAN.md` §7），再升 peer 并发布 minor。
- **配置兼容**：新增字段必须有默认值；删除字段至少保留一个大版本并告警。
- **数据兼容**：SQLite 用 `PRAGMA user_version`，每次迁移单向、可重入；不删除旧列，先写新列再切读。

---

## 6. 与 Harness 现有契约的对应关系

| 我们的概念 | Harness 对应物 | 关系 |
|---|---|---|
| bridge 插件 | Cordis plugin（`name`/`inject`/`Config`/`apply`） | 遵循其生命周期与 effect 规则 |
| bundle 分发 | `dsh.bundle.patch` + `dsh plugin` 安装 | 不写用户主 `cordis.yml` |
| 工具 | `ctx.tools.register(defineTool(...))` | 只读工具，不需要 approval |
| 上下文注入 | `agent/pre-step` + `createUserMessage({ source })` | 必须带 `source`，会进入会话日志 |
| 服务 | `ctx.provide('copilot', ...)` | 卸载即释放 |
| 凭证 | Harness `credentials` 服务 | H2 才复用；否则用 `safeStorage` |
| 知识 | 无 | 自建；可选以 MCP server 暴露给 `dsh-mcp-resources` |

任何在 Harness 侧新增的 "model-visible" 内容，都必须按 Harness 的规则变成可重建的会话事实；我们不新增 SessionEvent，只新增带 `source` 的 user message。
