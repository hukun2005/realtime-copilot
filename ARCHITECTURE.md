# RealTime Copilot 目标架构

本文描述**要建的东西**，不是现状。现状与证据见 [ARCHITECTURE_REVIEW.md](ARCHITECTURE_REVIEW.md)。
所有接口细节见 [PLUGIN_API.md](PLUGIN_API.md)；阶段与验收见 [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)；测量方法见 [TEST_PLAN.md](TEST_PLAN.md)。

---

## 1. 目标与非目标

### 1.1 目标

- 在 Windows 10/11 上采集**电脑输出音频**（会议软件播放的声音），可选叠加麦克风。
- 实时流式转写，识别"需要我回答的问题"。
- 用 DeepSeek 流式生成**口语化、可直接念出来**的短答案。
- 桌面悬浮窗常驻显示，不抢焦点、不被误触。
- 两种运行形态共享同一个 Core：**独立运行**与 **Harness 集成**。

### 1.2 非目标（v1 明确不做）

- 不向会议软件注入文字、不自动发言、不模拟键鼠。没有这个代码路径。
- 不做说话人分离（diarization）。只区分"远端（loopback）"与"本地（麦克风）"两个来源。
- 不做实时翻译、不做会议纪要导出（后续版本）。
- 不做云端多租户、不做账号体系。
- 不修改 DeepSeek Harness 仓库的任何源码。

---

## 2. 双模式

| | Standalone | Harness 集成（H1） | Harness 深度（H2，可选） |
|---|---|---|---|
| 谁启动 | 用户启动 Copilot 桌面应用 | 先启动 Copilot，再启动/连接 dsh | 同 H1 |
| 音频/ASR/检测 | Copilot Core | Copilot Core | Copilot Core |
| 答案由谁生成 | Core 内置 DeepSeek 客户端（自有 key） | 同左 | **Harness `ctx.llm`**（复用用户已配置的模型与凭证） |
| 会议内容进入 Harness | — | 用户显式开启后，经 `agent/pre-step` 注入当前问题与最近转写 | 同左 |
| Harness 能主动查会议 | — | 是，`copilot_*` 工具 | 是 |
| UI | Copilot 悬浮窗 | Copilot 悬浮窗（+ 可选极简 Harness 面板） | 同 H1 |

**为什么 H1 是默认集成形态**：它把"会议实时性"和"agent 的模型配置"解耦。用户不装 Harness 也能用；装了 Harness 也只是多了一条"会议内容可被 agent 看见/查询"的通路。H2 依赖 `ctx.llm.stream()` 的第三方可用性（未知问题 U6），在 Phase 7 实测后再决定是否落地。

**模式判定**：Core 不关心模式。模式只存在于两个适配器——`LlmProvider`（`deepseek-direct` vs `harness-llm`）与 bridge 插件是否存在。配置项 `mode: standalone | harness` 只影响默认值与 UI 文案，不改变事件协议。

---

## 3. 进程拓扑

```text
┌──────────────────────────────────────────────────────────────────────┐
│ Electron main (Node)                                                  │
│  · 生命周期 supervisor：拉起/重启 audio-capture 与 copilot-core        │
│  · 托盘、开机自启、单实例、全局快捷键                                  │
│  · safeStorage(DPAPI) 持有 DeepSeek key；经首次 hello 帧交给 Core      │
│  └─ BrowserWindow: 悬浮窗 (Vue3, 透明/置顶/点击穿透/内容保护)          │
│  └─ BrowserWindow: 设置窗口 (Vue3)                                    │
└───────────────┬───────────────────────────────┬──────────────────────┘
                │ WS 127.0.0.1:/ws/v1/events     │ spawn / named pipe
                │ (JSON 事件, 首帧鉴权)           │
┌───────────────▼───────────────┐   ┌───────────▼──────────────────────┐
│ copilot-core (Python/FastAPI)  │   │ audio-capture (C# / NAudio)      │
│  · 会话状态机                  │◄──┤  · WASAPI loopback (设备/进程)   │
│  · VAD → ASR → 转写装配        │WS │  · 可选麦克风                    │
│  · 问题检测                    │二进制│  · 16k mono PCM16 分帧          │
│  · 答案生成 (LlmProvider)      │   │  · 设备热插拔/静音检测           │
│  · 事件总线 / SQLite           │   │  · 崩溃自恢复                    │
└───────────────┬───────────────┘   └──────────────────────────────────┘
                │ (H2 only) 回环 RPC：请求 bridge 代调 ctx.llm
┌───────────────▼──────────────────────────────────────────────────────┐
│ dsh (Node, Cordis)                                                    │
│  └─ @realtimecopilot/dsh-copilot-bridge (TS plugin/bundle)            │
│       · WS 客户端连 Core；订阅 question/answer/transcript              │
│       · 注册工具 copilot_transcript / copilot_last_answer / copilot_ask│
│       · 用户开启时经 agent/pre-step 注入会议上下文                     │
│       · (H2) 暴露本地回环端点，代 Core 调用 ctx.llm.stream()           │
└──────────────────────────────────────────────────────────────────────┘
```

**进程隔离的理由**：WASAPI 驱动异常或设备热插拔可能让采集进程崩溃；ASR/LLM 的网络抖动不应影响悬浮窗渲染；Core 崩溃不应让用户失去"当前已有答案"的可见性。supervisor 对三者分别重启，UI 侧保留最近状态快照。

**为什么 Core 是 Python**：中文流式 ASR 的成熟 SDK 与本地模型（sherpa-onnx / FunASR / 厂商 SDK）主要在 Python 生态；Harness 自身也已把 Python 运行时纳入发布形态（`apps/desktop` 的 `resources/runtime`），打包有先例。代价是多一次本机回环（实测应在个位数毫秒），见 §6 与未知问题 U5。

---

## 4. Copilot Core 内部

### 4.1 流水线

```text
audio frame (20ms, 16k mono PCM16, source=loopback|mic)
  → RingBuffer + 语音活动检测 (VadProvider)
  → 语音段切分：START / CONTINUE / END（END 附带最后有效帧时间戳）
  → AsrProvider.stream(segment)  ── partial* ──► transcript.partial   (仅 UI 显示)
                                  └─ final  ──► transcript.segment    (进入装配)
  → TranscriptAssembler：滚动窗口、按来源分轨、标点归一、去重
  → QuestionDetector：启发式打分 → (可选) 小模型复核
  → question.detected { text, confidence, source, anchorTs }
  → AnswerEngine：
        上下文 = 系统提示 + 最近 N 条转写 + KB 命中 + 用户画像
        LlmProvider.stream() → answer.start / answer.delta* / answer.end
  → 事件总线下发；SQLite 落库（答案与问题；音频默认不落盘）
```

### 4.2 关键设计点

- **锚点定义**：`anchorTs` = VAD 判定语音结束时刻（最后一帧有效音频的采集时间戳），不是"ASR 返回 final 的时刻"。目标延迟以它为起点计算，这样 ASR 的定稿延迟被如实计入，不能靠"晚一点算起点"美化数字。
- **partial 提前触发**：ASR partial 已出现完整问句时，可提前发起 LLM 请求（见 U10）。默认关闭，Phase 9 用 A/B 决定；开启时必须能在 final 与 partial 不一致时取消并重发，且不得产生两条答案。
- **取消与替换**：新问题到达时取消上一条未完成的答案流（`AbortController` 语义），UI 标记"已过期"。同一问题在 800ms 去抖窗口内重复触发只保留一次。
- **多来源**：`source` 字段贯穿全链路。默认只对 `loopback`（远端）的问题生成答案；`mic`（自己）的问题默认忽略，可配置。
- **KB 检索**：v1 用 SQLite FTS5（中文分词用 `unicode61` + 关键词表），可选向量检索作为 `KnowledgeProvider` 的第二实现。检索超时上限 120ms，超时即跳过，不阻塞答案。
- **模型调用**：`max_tokens` 200–260，`temperature` 0.4–0.6，系统提示要求"口语化、不超过 3 句、先说结论"。提示词模板与版本号一起落库，便于回溯。

### 4.3 会话与状态

一次"会议会话"（meeting session）从用户点击"开始采集"创建，到停止或 5 分钟静音自动结束。会话内状态：`idle → capturing → listening → answering → ...`，所有状态变化都发事件，UI 与 bridge 都只依赖事件，不轮询。

---

## 4A. 采集控制面（Phase 2-C）

采集不再由 CLI 参数决定，而是由 Core 的状态机驱动。Core 是唯一权威：UI 只能发起命令，
sidecar 的状态上报只用于确认，不能跳步。

```text
UI/bridge ──capture.start(requestId)──▶ Core.CaptureController
                                          │ ① IDLE → STARTING
                                          │ ② tickets.begin_session() + 签发票据
                                          │ ③ spawn: dotnet audio-capture.dll（RTC_CAPTURE_CONTROL=1, RTC_AUDIO_TICKET=…）
                                          ▼
                                       sidecar ──hello(role=audio, ticket)──▶ Core 校验并 attach
                                          │ ◀──capture.start(loopback/mic)────┘
                                          │ ④ 打开 WASAPI → audio.status{starting}
                                          │ ⑤ audio.status{capturing} ─────────▶ Core: STARTING → CAPTURING → 广播 UI
                                          │ ⑥ 50fps 二进制音频帧 ──────────────▶ AudioHub 统计
UI/bridge ──capture.stop(requestId)───▶ Core: CAPTURING → STOPPING → 下发 capture.stop
                                          │ ◀──audio.status{idle}──────────────┘ → IDLE + end_session()
```

状态与合法迁移：

| 状态 | 允许迁移 | 触发 |
|---|---|---|
| `idle` | `starting` | capture.start |
| `starting` | `capturing` / `stopping` / `error` | sidecar 上报 / 启动超时 / spawn 失败 |
| `capturing` | `stopping` / `error` | capture.stop / 设备丢失、sidecar 崩溃 |
| `stopping` | `idle` / `error` | sidecar 上报 idle / 停止超时 |
| `error` | `idle` / `starting` | capture.stop 复位 / 重试 start |

约束（有对应测试）：

- **不自动恢复**：Core 启动永远是 `idle`；没有 `capture.start` 不会拉起任何采集进程。
- **幂等**：重复 start（已在 starting/capturing）不再 spawn；重复 stop（idle/stopping）只回状态。
- **超时**：`capture.start_timeout_ms`（默认 8s）与 `capture.stop_timeout_ms`（默认 5s），超时进 `error` 并强杀进程。
- **断连即停**：音频连接断开、sidecar 退出都进入 `error`（`audio_disconnected` / `sidecar_exited`），不会静默重连。
- **票据**：每次 start 新签一次性票据，stop/崩溃/关闭即撤销（见 `SECURITY.md §7`）。
- **设备变化**：控制模式下 sidecar 每 2s 检查默认端点；变化时上报 `device_changed` 并停止，不静默切换来源。

## 5. 延迟预算（设计约束，非实测结果）

| 环节 | 预算 (P50) | 说明 |
|---|---|---|
| 采集到帧进入 Core | 5–15 ms | WASAPI 缓冲 + 本机 WS |
| VAD 判定段结束 | 30–60 ms | 帧级能量 + 小模型；含 200–300ms 尾静音，但**不计入**（锚点在段尾） |
| ASR 定稿 | 150–400 ms | 供应商差异最大；本地模型更慢 |
| 转写装配 + 去重 | < 10 ms | 纯内存 |
| 问题检测 | 5–15 ms（启发式）/ 80–150 ms（LLM 复核） | 默认只走启发式 |
| KB 检索 | 0（未命中）/ ≤ 120 ms | 超时即跳过 |
| 组 prompt + 建连 | 10–30 ms | 连接复用（keep-alive）后更低 |
| DeepSeek 首 token | 300–700 ms | **最大不确定项**，必须实测（U1） |
| 下发 + 渲染 | 10–20 ms | |
| **合计（P50）** | **≈ 0.55–1.35 s** | 目标 1s 处于预算下沿，属"紧但可能" |

**明确说明**：上表是**预算拆分**，不是测量结果。当前没有任何实现，因此没有任何 P50/P95 数字。达标判定与发布门槛见 [TEST_PLAN.md](TEST_PLAN.md) §5。

**达不到时按此顺序降级**（不得伪造数据）：
1. 缩短提示词与 `max_tokens`；
2. 关闭 LLM 复核与 KB 检索；
3. 换更快的 ASR 或启用 partial 提前触发；
4. 如实公布新的 P50/P95 并调整产品承诺。

---

## 6. 数据模型（SQLite，WAL）

```sql
meeting_session(id TEXT PK, started_at INTEGER, ended_at INTEGER,
                mode TEXT, capture_source TEXT, app_version TEXT)
transcript_segment(id TEXT PK, session_id TEXT, source TEXT, start_ms INTEGER,
                   end_ms INTEGER, text TEXT, is_final INTEGER, asr_provider TEXT,
                   confidence REAL, created_at INTEGER)
question(id TEXT PK, session_id TEXT, segment_id TEXT, text TEXT, confidence REAL,
         detector TEXT, anchor_ts INTEGER, created_at INTEGER)
answer(id TEXT PK, question_id TEXT, text TEXT, model TEXT, prompt_version TEXT,
       first_token_ms INTEGER, total_ms INTEGER, token_usage TEXT, status TEXT)
latency_sample(id INTEGER PK AUTOINCREMENT, session_id TEXT, stage TEXT,
               anchor_ts INTEGER, first_token_ts INTEGER, total_ts INTEGER)
kb_document(id TEXT PK, title TEXT, source_path TEXT, sha256 TEXT, updated_at INTEGER)
kb_chunk(id TEXT PK, document_id TEXT, ordinal INTEGER, text TEXT)
kb_fts(chunk_id, text)              -- FTS5 虚表
settings(key TEXT PK, value TEXT, updated_at INTEGER)
```

**原始音频默认不落库、不落盘。** 仅当用户显式开启"调试录音"时写入 `%LOCALAPPDATA%\RealTimeCopilot\debug-audio\<session>.wav`，并在 UI 与日志中明示保留期限（默认 24 小时，启动时清理）。

---

## 7. 安全与隐私模型

| 要求 | 实现 |
|---|---|
| 录音由用户主动开启 | 启动后状态为 `idle`；`start` 必须来自用户手势（按钮/快捷键/托盘菜单），无自动开始、无开机自动采集 |
| 采集状态可见 | 悬浮窗常驻状态点（采集中=红点+计时）；托盘图标同步；停止前不隐藏指示 |
| 默认不持久化原始录音 | 内存环形缓冲（最近 30s，仅用于重连与调试）；落盘需二次确认 |
| API Key 安全存储 | Electron `safeStorage`（Windows DPAPI）加密后存 `%APPDATA%\RealTimeCopilot\secrets.bin`；内存中只在启动时解密一次；日志与事件永不含 key |
| 本地服务仅 127.0.0.1 | Core 与 sidecar 均显式绑定 `127.0.0.1`；启动自检监听地址；端口冲突时选择临时端口并写 `runtime.json` |
| 未授权不向会议发消息 | 代码库不含键鼠注入、不含 UI 自动化依赖；`package.json` 用依赖审查守住这一点 |
| 悬浮窗不被会议捕获 | `setContentProtection(true)` + 默认开启"共享时隐藏"；设置中可关（并告知风险） |
| Harness 注入可控 | 默认关闭；开启时提示"会写入 Harness 会话日志并可能上传"（Harness 的 model-visible ⟺ logged 规则） |
| 凭证不跨进程明文传输 | Core 的首帧 `hello` 携带一次性 token；DeepSeek key 由 Electron 在启动时通过本地一次性握手交给 Core，不经命令行参数、不写日志 |

**鉴权**：Core 启动时生成 32 字节随机 token，写入 `%APPDATA%\RealTimeCopilot\runtime.json`（`0600`，Windows 下用 `safeStorage` 加密）。UI 与 bridge 必须在连接后 2 秒内发送 `hello{token}`，否则断连。禁止无鉴权端点。

---

## 8. Harness 集成（H1）

### 8.1 bridge 做什么

1. **连接**：作为 WS 客户端连 `ws://127.0.0.1:<port>/ws/v1/events`，读 `runtime.json` 拿端口与 token。Core 不在时进入"未连接"状态，不影响 dsh 正常使用。
2. **查询工具**（模型可调用，只读）：
   - `copilot_transcript { minutes?: number }` — 返回最近转写文本。
   - `copilot_last_answer {}` — 返回最近一条答案与它对应的问题。
   - `copilot_ask { question }` — 就会议内容提问，转发给 Core 生成答案（会消耗模型额度，工具描述中声明）。
3. **上下文注入**（默认关闭）：在 `agent/pre-step` 中，把当前未回答的问题 + 最近转写以 `source: { kind: 'realtime-copilot', form: 'snapshot', sections }` 的 user message 注入。遵守 Harness 规则：模型可见 ⟺ 进入会话日志，所以必须在设置里明示。
4. **状态**：`ctx.copilot` 服务暴露 `status()`（连接状态、采集状态、最近事件时间），供其他插件/UI 读取。

### 8.2 交付形态

一个 npm 包 `@realtimecopilot/dsh-copilot-bridge`，同时具备：

- 插件入口（`lib/index.js`，`name`/`inject`/`Config`/`apply`）；
- bundle 声明（`dsh.bundle.patch` 指向一行 `insert`），使 `dsh plugin` 能安装并启用；
- 不包含任何 client（React）代码，避免绑定 Harness 的 UI 版本。

### 8.3 不做的事

- 不把 Core 塞进 Harness 进程（语言与崩溃域都不合适）。
- 不依赖 Harness 的 Web `/api`。
- 不注册会话事件、不改 `SessionEventMap`——避免触发 Harness 的持久化格式迁移契约。

---

## 9. 失败与降级

| 故障 | 表现 | 行为 |
|---|---|---|
| 采集设备消失 | `capture.error` 事件 | 自动重试 3 次（1s/2s/4s），失败则状态转 `error` 并在悬浮窗显示"请选择设备" |
| 采到静音 | 音量持续 -60dBFS > 5s | 发 `capture.silent` 提示（常见原因：会议软件走了受保护音频路径） |
| ASR 断流 | `asr.error` | 重连（指数退避，上限 3 次）；期间 UI 显示"字幕暂停"；已生成答案不受影响 |
| LLM 超时/失败 | `answer.error` | 不重试首答（重试会破坏延迟承诺），显示"答案生成失败，可手动重试" |
| Core 崩溃 | UI 与 bridge 断连 | supervisor 重启 Core；UI 显示"正在恢复"；不丢历史答案（SQLite） |
| bridge 未连接 | 工具返回明确错误 | `copilot_*` 工具返回结构化错误码，不抛未捕获异常 |
| 端口被占用 | Core 启动失败 | 改用临时端口并更新 `runtime.json`；UI 与 bridge 重新读取 |

---

## 10. 架构决策记录（ADR 摘要）

| ID | 决策 | 备选 | 理由 |
|---|---|---|---|
| D1 | Core 用 Python | TS Core 内嵌 Harness 插件 | ASR 生态 + 与 UI/采集解耦；代价是本机回环，可测量 |
| D2 | 三进程（UI/Core/Audio） | 单进程 | 故障隔离；驱动崩溃不带走 UI |
| D3 | 采集用 C# + NAudio | Python sounddevice / Node ffi | WASAPI 进程级 loopback 在 C# 上最成熟；NAudio 有现成实现 |
| D4 | 事件用 WS + JSON，音频走二进制帧 | gRPC / 命名管道 | 跨语言实现成本最低；本机开销可忽略；命名管道留作优化 |
| D5 | 悬浮窗 Vue3，独立 Electron | 嵌入 Harness React UI | Harness 无 Vue；悬浮窗是独立产品形态 |
| D6 | 知识库自建 SQLite FTS5 | 复用 Harness 上下文/技能 | Harness 无 KB 接口；FTS5 零依赖、够用 |
| D7 | 锚点取 VAD 段尾时间戳 | 取 ASR final 返回时刻 | 后者会系统性隐藏 ASR 延迟，是自欺 |
| D8 | v1 不做输入注入 | 自动把答案贴进会议聊天 | 安全要求明确禁止未授权自动发送；且无此代码路径最安全 |

---

## 11. 开放问题

见 [ARCHITECTURE_REVIEW.md](ARCHITECTURE_REVIEW.md) 第 10 节的 U1–U10。其中 U1/U3/U6 会直接改变架构判断，必须在前三个相关阶段结束时给出结论并回写本文档。
