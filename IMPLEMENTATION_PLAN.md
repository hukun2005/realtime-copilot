# 实施计划

十个阶段，每个阶段都有明确的文件、任务、依赖、验收标准与测试命令。
**阶段之间是门禁关系**：上一阶段的验收未通过，不进入下一阶段（例外：Phase 6 可与 Phase 5 并行，Phase 7 可与 Phase 8 并行，见依赖图）。

所有命令以仓库根 `realtime-copilot/` 为工作目录。标注 `[计划]` 的命令在对应阶段建立后才有意义，当前不可执行。

---

## 0. 仓库结构（目标）

```text
realtime-copilot/
├─ README.md  ARCHITECTURE.md  ARCHITECTURE_REVIEW.md
├─ IMPLEMENTATION_PLAN.md  PLUGIN_API.md  TEST_PLAN.md  BUILD_LOG.md
├─ packages/
│  ├─ protocol/                 # 事件 JSON Schema + TS/Python 生成代码
│  └─ dsh-copilot-bridge/       # Harness Cordis 插件（TS）
├─ services/
│  ├─ copilot-core/             # Python FastAPI：编排/ASR/检测/答案/SQLite
│  └─ audio-capture/            # C# + NAudio：WASAPI 采集 sidecar
├─ apps/
│  └─ desktop/                  # Electron + Vue3：悬浮窗/设置/supervisor
├─ tools/                       # 延迟基准、fixture 录制、KB 导入
└─ tests/
   └─ fixtures/                 # 音频、转写、问答样本（不含真实隐私数据）
```

依赖方向（禁止反向）：`apps/desktop` → `packages/protocol` ← `packages/dsh-copilot-bridge`；`services/copilot-core` → `packages/protocol`；`services/audio-capture` → 无（只依赖线上协议）。

---

## 阶段依赖图

```text
P1 基础工程
 ├─► P2 音频采集 ─► P3 流式 ASR ─► P4 问题检测 ─► P5 DeepSeek 问答 ─► P9 性能优化 ─► P10 打包
 ├─► P6 悬浮窗（可在 P3 后并行）
 └─► P7 Harness 集成（可在 P5 后并行） ─► P8 独立运行 ─┘
```

---

## Phase 1 — 基础工程

**目标**：建立可编译、可测试、可观测的空壳工程，把协议、配置、日志、错误模型先定死。不写音频、不接模型。

**依赖**：无（Phase 0 已完成）。

**文件**

| 路径 | 内容 |
|---|---|
| `packages/protocol/schema/events.v1.json` | 全部事件与配置的 JSON Schema（唯一事实源） |
| `packages/protocol/scripts/gen.ts` | 由 schema 生成 `src/generated.ts` 与 `python/copilot_protocol/models.py` |
| `packages/protocol/src/index.ts` | 信封类型、type 常量、校验函数 |
| `packages/protocol/python/copilot_protocol/{__init__,models,envelope}.py` | pydantic v2 模型 |
| `services/copilot-core/pyproject.toml` | `requires-python = ">=3.10"`，fastapi、uvicorn、pydantic v2、pytest、ruff |
| `services/copilot-core/src/copilot_core/{__main__,app,config,logging,errors,ws,events}.py` | 空 pipeline + WS 握手 + 鉴权 |
| `services/copilot-core/src/copilot_core/storage/{db,migrations}.py` | SQLite 建表与 `user_version` 迁移 |
| `services/copilot-core/tests/{test_config,test_ws_auth,test_protocol_roundtrip}.py` | |
| `services/audio-capture/AudioCapture.csproj` | net8.0、NAudio |
| `services/audio-capture/src/{Program,Protocol}.cs` | 只连 Core、发心跳，不采集 |
| `apps/desktop/package.json` + Vite/Vue3 骨架 | 空窗口 + 连 Core 显示状态 |
| `tools/gen-runtime-json.ts` | 写 `runtime.json`（端口 + token） |
| `.github/workflows/ci.yml` | lint + test（三语言） |

**任务**

1. 用 JSON Schema 定义 `hello`、`system.ready`、`system.error`、`ping/pong`、配置对象；生成双端类型。
2. Core 实现 WS 鉴权（token、2s 超时、role 校验）、心跳、`runtime.json` 原子写（`0600`）。
3. Core 实现配置加载（TOML + `RTC_*` 覆盖）与 fail-loud 校验。
4. 统一错误模型 `CopilotError(code, message, retryable)` 与事件化的错误上报。
5. 结构化日志（JSON lines，字段：ts/level/module/event/session_id，**禁止记录 key 与音频内容**）。
6. CI：`pytest`、`dotnet test`、`vitest`、`vue-tsc --noEmit`。

**验收标准**

- [ ] 冷启动 Core < 1.5s，`GET /healthz` 返回 200 且只监听 `127.0.0.1`。
- [ ] 无 token / 错 role / 超时 2s 三种情况均被拒绝并关闭连接，有测试覆盖。
- [ ] `runtime.json` 权限为 `0600`；不含明文 key。
- [ ] 事件 schema 双端往返一致（Python 生成 → TS 解析 → 回写，字段无损）。
- [ ] 三个语言的 lint/test 在 CI 全绿。
- [ ] 配置非法值（如 `server.host="0.0.0.0"`）启动即失败并给出字段路径。

**测试命令** `[计划]`

```bash
uv run --project services/copilot-core pytest services/copilot-core/tests -q
dotnet test services/audio-capture/tests -c Release
pnpm -C packages/protocol test
pnpm -C apps/desktop typecheck
```

**退出条件**：以上命令全绿，且 `BUILD_LOG.md` 记录基线（启动耗时、内存）。

---

## Phase 2-A — 音频二进制协议与 Core 接收通道（已完成）

**目标**：让模拟音频通过真实 C# 客户端以 Binary Frame 送达真实 Python Core，完成解析、权限、校验、统计与错误处理。不实现 WASAPI、不接 ASR。

**依赖**：Phase 1。

**交付**

| 路径 | 内容 |
|---|---|
| `packages/protocol/audio-frame.v1.md` | 协议文档：28 字节头、字段约束、序号/时间戳语义、兼容与失败行为 |
| `packages/protocol/fixtures/audio/` | 7 个二进制样本 + 元数据；由 `scripts/make-audio-fixtures.py` 确定性生成 |
| `services/copilot-core/src/copilot_core/audio/` | `frame.py` 解析/编码、`session.py` 连续性/限速、`sink.py`、`stats.py`、`hub.py` |
| `services/copilot-core/src/copilot_core/ws/connection.py` | 二进制分发、角色限制、断开阈值、会话生命周期 |
| `services/audio-capture/src/Audio/` | `AudioFrameCodec`、`SimulatedAudioSource`、`AudioStreamer`、`SimulationOptions` |
| `scripts/e2e-audio.ps1` | 真实 Core + 真实客户端 60 秒音频传输与丢帧检测 |
| `.github/workflows/ci.yml` | Windows 作业新增 60 秒音频端到端 |

**验收结果**（实测，见 `BUILD_LOG.md`）

- [x] 协议文档完整，头部布局、字节序、长度上限、版本兼容与失败行为均有定义。
- [x] Python 解析真实 C# 发送的帧：668 字节、字段与 fixtures 一致。
- [x] 60 秒稳定传输：2995 帧，与发送数完全一致。
- [x] 帧数量统计正确（`frames` / `bytes` / `sinkFrames`）。
- [x] 丢帧检测正确：主动跳过 6 帧，Core 报告 `gaps=6`。
- [x] 非 audio 角色发送二进制帧被拒绝（`invalid_role` + 1008）。
- [x] 超大帧被拒绝（`invalid_payload` + 1009）。
- [x] Phase 1 全部测试继续通过。

**测试命令**

```bash
uv run pytest services/copilot-core/tests -q
dotnet test services/audio-capture/tests -c Release
pwsh -File scripts/e2e-audio.ps1 -Seconds 60 -DropEvery 500
```

---

## Phase 2-B — Windows 音频采集（WASAPI，设备级）

**状态**：已完成设备级 loopback 与可选麦克风；进程级 loopback 顺延到 Phase 2-C（见该节）。

**目标**：把模拟源换成真实采集：系统输出 + 可选麦克风，统一到 16k/mono/20ms 帧并发给 Core。

**依赖**：Phase 2-A（协议与接收通道已就绪）。

**交付**

| 路径 | 内容 |
|---|---|
| `services/audio-capture/src/Capture/WasapiCaptureSource.cs` | `WasapiRecorderBuilder` 打开设备（NAudio 3.3 起 `WasapiCapture` 已过时）→ 缓冲 → 重采样 → 分帧 |
| `services/audio-capture/src/Audio/AudioFramer.cs` | 跨回调残留字节累积、20ms 切帧、按帧数推进时间戳 |
| `services/audio-capture/src/Audio/Pcm16.cs` | float → PCM16LE，带限幅 |
| `services/audio-capture/src/Audio/FrameStreamer.cs` | 采集事件 → 有界队列 → 顺序发送；队列满丢最旧帧 |
| `services/audio-capture/src/Audio/CaptureOptions.cs` | `--capture-loopback`、`--capture-mic`、`--capture-device`、`--mic-device` |
| `services/copilot-core/src/copilot_core/audio/level.py` | 峰值/RMS dBFS，写入 `/healthz` 的 `peakDbfs` / `lastPeakDbfs` |
| `scripts/capture-smoke.ps1` | 播放测试音 → 真实采集 → 校验帧数与非静音电平 |
| `tools/tone-player/` | 诊断工具：通过 WASAPI 播放测试音（不随产品发布） |

**实测结果**（本机，见 `BUILD_LOG.md`）

- 8 秒 loopback 采集：398 帧、0 丢帧、0 无效帧，整段峰值 **-8.7 dBFS**（测试音理论值 -8.7 dBFS）。
- 显式指定端点采集同样正常（峰值 -10.5 dBFS），修复了"枚举器提前 Dispose 导致端点失效、采到静音"的问题。
- 采集按 50fps 限速后不再触发 Core 的 `rate_limited`。

**未完成 / 未验证**（不在"已完成"范围内）

- [ ] 进程级 loopback（`WasapiRecorderBuilder.WithProcessLoopback`）——Phase 2-C。
- [ ] 30 分钟稳定性与内存增长。
- [ ] 4 核 CPU 占用 < 5%。
- [ ] 腾讯会议 / 微信 / 浏览器三场景实机记录。
- [ ] 设备热插拔自动切换。
- [ ] 静音 5s 事件（当前只有电平统计，没有 `capture.silent` 事件）。
- [ ] 采集进程被强杀后的 supervisor 拉起。
- [ ] 麦克风实机验证（代码路径已接，未用真实麦克风测试）。

**风险**：R5（进程级 loopback 的 Win10 build 门槛）、R6（受保护音频）。设备级采集不依赖进程级 API，已在 Win11 本机验证可用。
## Phase 2-C — 采集控制面、安全与诊断（已完成）

**状态**：控制面、票据、统计、诊断与稳定性脚本已完成；进程级 loopback 未实现（转到 Phase 2-D）。

**目标**：把 CLI 启动的采集升级为可被 UI 控制、被 Core 管理、状态可观测、错误可恢复的服务。

**交付**

| 路径 | 内容 |
|---|---|
| `services/copilot-core/src/copilot_core/capture/state.py` | 五状态机 + 合法迁移表 + `InvalidTransition` |
| `services/copilot-core/src/copilot_core/capture/controller.py` | sidecar 生命周期、启动/停止超时、幂等、断连与崩溃处理、状态广播 |
| `services/copilot-core/src/copilot_core/tickets.py` | HMAC 票据：会话绑定、一次性 nonce、过期、撤销 |
| `services/copilot-core/src/copilot_core/ws/connection.py` | 票据校验、capture.start/stop 路由、audio.status 接收、`send_control` |
| `services/audio-capture/src/Control/CaptureControl.cs` | 控制模式执行器：动态启停采集、状态上报、设备变化检测 |
| `services/audio-capture/src/Audio/SimulatedFrameSource.cs` | 无端点环境（CI）用的静音帧源，仅控制模式且显式开启时使用 |
| `scripts/control_client.py` | ui 角色驱动 start/stop/session 的测试客户端 |
| `scripts/e2e-control.ps1` | 真实 Core + 真实 sidecar + 真实控制消息的端到端 |
| `scripts/capture-stability.ps1` | 30 分钟稳定性采样与报告 |
| `tools/capture-probe.ps1` | 机器可读诊断报告（系统版本、设备、参数、统计） |

**验收（实测，见 `BUILD_LOG.md`）**

- [x] 状态机合法/非法迁移、并发与重复命令、启动/停止超时、崩溃与断连均有测试。
- [x] P2-C.1 加固：asyncio.Lock 串行化生命周期、会话代次校验、统一失败收尾（撤销票据/终止进程/取消任务）、迟到状态忽略。
- [x] P2-C.2 加固：锁外 spawn（三阶段确认）、后台任务按会话绑定、disconnect 竞态快照、票据到期即失效与输入上限。
- [x] 真实 Core + 真实 sidecar：`capture.start → capturing → 300 帧 → capture.stop → idle`，无进程残留。
- [x] 票据：会话绑定、过期、重放、篡改、跨会话均拒绝。
- [x] 非 audio 角色发音频帧仍被拒绝；audio 角色不能下发 capture 命令。
- [x] 30 分钟稳定性脚本产出 JSON/CSV 报告。
- [x] `capture-smoke.ps1` 三态判定（PASS/SKIP/FAIL）并区分错误码。

**未完成**

- [ ] 进程级 loopback（`WithProcessLoopback`）——Phase 2-D；本阶段只做能力探测接口预留。
- [ ] 设备热插拔自动切换（当前是"检测到变化→报错并停止"，符合"不静默切换"的要求）。
- [ ] 腾讯会议/微信/浏览器三场景实机记录。
- [ ] 30 分钟测试的结论需要按目标机器复测（本机为 Windows 11 26300 + Senary 虚拟声卡）。

## Phase 2-D — 音频可靠性增强（部分完成，可选增强）

**状态**：按来源统计、Channel 并发模型与稳定性报告已完成；设备热插拔恢复与进程级 loopback 未实现。

**已完成**

| 项 | 内容 |
|---|---|
| 按来源统计 | `AudioSourceStats` + `AudioSession`/`AudioHub`：loopback 与 mic 独立计数、峰值/RMS、静音时长、时间戳、采样率/声道；`audio.status.sources` 与 `/healthz.audio.sources` |
| C# 并发模型 | 有界 `Channel<AudioFrame>`（容量 200）+ 单消费者；回调只入队；满载 `DropWrite` 可计数；停止顺序：取消 → 源 Stop → 发送任务退出 |
| 稳定性报告 | `scripts/capture-stability.ps1`：Git SHA、sidecar 版本、模式、起止时间、理论/实际帧数、丢帧/重复/无效、CPU/内存、状态迁移、sidecar 退出码；输出 `artifacts/capture-stability` |

**未完成**

- 设备热插拔自动恢复已实现策略（默认设备 3 次退避恢复、显式设备不静默切换）；真实拔插与目标应用验证仍为 NOT_VERIFIED。
- 进程级 loopback 已有 --probe-process-loopback 能力探测；腾讯会议/微信实机验证（NOT_VERIFIED）。
- 真实 WASAPI 30 分钟与模拟 30 分钟的双跑结果：见 `BUILD_LOG.md`（只记录实际完成的运行）。

## Phase 3 — 流式 ASR

**状态**：协议与接口准备完成（P3-A/B/C/D）；识别引擎未接入。

**已完成**

| 项 | 内容 |
|---|---|
| P3-A 协议冻结 | `asr.status` / `asr.partial` / `asr.final` / `asr.error` schema、fixtures、TS 与 Pydantic 生成 |
| P3-B 状态分离 | `AsrStatusTracker` 独立于采集状态机；ASR error 不改变 capture 状态（有测试） |
| P3-C 票据 | `TicketIssuer.issue(purpose, generation)`；audio/asr 隔离、一次性、过期、旧会话拒绝 |
| P3-D VAD | `EnergyVad` + `Segmenter`：per-source 状态、完整 PCM 帧 Ring Buffer、pre-roll、超长切分 |
| P3.1 运行时 | `AsrPipeline` 有界帧/片段队列接入 `AudioSession`；`asr.status` idle 广播；段缓存上限 |
| P3.1.1 加固 | Peak/RMS 统一口径、双指标 VAD、Provider 抽象、PCM 预算与生命周期 |

**未完成**

- 真实 ASR provider（DeepSeek / 云厂商 / sherpa-onnx）与转写装配。
- `SileroVad`、`VadProvider` 协议外的模型加载。
- `asr.status` 的实际广播与 UI 接线（当前只有 tracker 与事件构造函数）。
- 延迟埋点与 `tools/asr_eval.py`。

**目标**：把语音段稳定转成文本，partial 可见、final 可定稿，并测量"段尾 → final"的延迟分布。

**依赖**：Phase 2（至少需要真实音频 fixture）；可先用录制 WAV 并行开发。

**文件**

| 路径 | 内容 |
|---|---|
| `services/copilot-core/src/copilot_core/providers/vad/{energy,silero}.py` | 两种 VAD |
| `services/copilot-core/src/copilot_core/providers/asr/base.py` | `AsrProvider` 协议 + 注册表 |
| `services/copilot-core/src/copilot_core/providers/asr/cloud_a.py` | 候选供应商 A（Phase 3 评测后确定） |
| `services/copilot-core/src/copilot_core/providers/asr/sherpa_local.py` | 本地流式模型，离线回退 |
| `services/copilot-core/src/copilot_core/pipeline/segmenter.py` | VAD 段状态机 |
| `services/copilot-core/src/copilot_core/pipeline/transcript.py` | 滚动窗口、按来源分轨、去重 |
| `services/copilot-core/tests/providers/test_asr_contract.py` | 契约测试（对 fake provider 与真 provider 各跑一遍） |
| `tools/asr_eval.py` | WER + 定稿延迟评测 |
| `tests/fixtures/audio/*.wav` + `*.txt` | 30–60s 会议片段与人工参考文本 |

**任务**

1. 定义并冻结 `AsrProvider` 契约（含错误、断流、重连语义）；写 fake 实现驱动上层。
2. 实现能量 VAD；Silero 作为可选（ONNX，CPU）。
3. 接入候选供应商 A；再接入本地 sherpa-onnx 作为离线回退。
4. 转写装配：partial 覆盖、final 追加、同段去重、跨段合并（同一说话人 <300ms 间隔）。
5. 延迟埋点：`asr_final_ms = final_ts - anchor_ts`，写 `latency_sample`。
6. `tools/asr_eval.py` 跑 20 段样本，输出 WER 表与 P50/P95 定稿延迟。
7. 断流演练：断网 10s、供应商 5xx、超时，各验证重连与降级。

**验收标准**

- [ ] 样本集 WER ≤ 15%（会议场景，含口音与术语）；本地模型 WER ≤ 20%。
- [ ] 定稿延迟（anchor → final）P50 ≤ 400ms、P95 ≤ 800ms（供应商 A）。
- [ ] 断网 10s 后自动恢复，期间 UI 显示 `asr.status=degraded`，无重复 final。
- [ ] 同一段不产生两条 final；partial 不会泄漏进答案上下文。
- [ ] 30 分钟连续转写无内存泄漏（RSS 增长 < 100MB）。

**测试命令** `[计划]`

```bash
uv run --project services/copilot-core pytest services/copilot-core/tests/providers -q
uv run --project services/copilot-core python tools/asr_eval.py --fixtures tests/fixtures/audio --provider cloud-a --out .artifacts/asr.json
uv run --project services/copilot-core python tools/asr_eval.py --fixtures tests/fixtures/audio --provider sherpa-local
```

**退出条件**：`asr.json` 记录 WER 与延迟；选择供应商并写进 `BUILD_LOG.md` 的决策记录（含被否选项与原因）。

---

## Phase 4 — 问题检测

**目标**：在转写流里稳定识别"需要我回答的问题"，可解释、可评估、误报可控。

**依赖**：Phase 3。

**文件**

| 路径 | 内容 |
|---|---|
| `services/copilot-core/src/copilot_core/pipeline/questions.py` | 检测编排、去抖、去重、置信度门槛 |
| `services/copilot-core/src/copilot_core/providers/detect/heuristic.py` | 疑问词/句式/标点/上下文 |
| `services/copilot-core/src/copilot_core/providers/detect/llm_review.py` | 小模型复核（可选，默认关） |
| `services/copilot-core/tests/detect/test_heuristic.py` | 20+ 正例、20+ 负例 |
| `tests/fixtures/detect/labeled.jsonl` | 人工标注：is_question、normalized_text |
| `tools/detect_eval.py` | Precision/Recall/F1 + 误报清单 |

**任务**

1. 启发式：疑问词（什么/怎么/为什么/是否/能不能/如何/请问/吗/呢）、句式（"我们是不是…"）、ASR 标点、上下文（上一句是陈述、这一句短且升调无法判断时降权）。
2. 说话人来源过滤：默认只对 `loopback` 判定；`mic` 默认忽略（可配置）。
3. 去抖与去重：800ms 窗口内相同问题只保留一条；跨段合并。
4. 可选 LLM 复核：仅当 `0.45 ≤ score < 0.7` 时触发，≤150ms 超时，超时用启发式结果。
5. 可解释：每次判定记录 `reasons`，随 `question.detected` 下发并落库。
6. 评测：F1 目标 ≥ 0.85，误报率 ≤ 10%。

**验收标准**

- [ ] 标注集上 Precision ≥ 0.85、Recall ≥ 0.80。
- [ ] 纯陈述句误报 ≤ 5%（"我们下周一开会"不得触发）。
- [ ] 反问/设问（"这难道不是问题吗？"）被正确识别为问题但不强制触发答案（置信度门槛可调）。
- [ ] 检测耗时 P95 ≤ 15ms（启发式）。
- [ ] 检测器崩溃不影响转写与采集（异常被隔离并降级为不检测）。

**测试命令** `[计划]`

```bash
uv run --project services/copilot-core pytest services/copilot-core/tests/detect -q
uv run --project services/copilot-core python tools/detect_eval.py --labels tests/fixtures/detect/labeled.jsonl --out .artifacts/detect.json
```

---

## Phase 5 — DeepSeek 问答与延迟埋点

**目标**：问题 → 口语化答案的流式链路打通；**首次产出真实的端到端延迟分布**（不是目标值）。

**依赖**：Phase 4。

**文件**

| 路径 | 内容 |
|---|---|
| `services/copilot-core/src/copilot_core/providers/llm/base.py` | `LlmProvider` 协议 + 注册表 |
| `services/copilot-core/src/copilot_core/providers/llm/deepseek_direct.py` | HTTP SSE 客户端（httpx，keep-alive） |
| `services/copilot-core/src/copilot_core/pipeline/answer.py` | 编排：上下文组装、取消、去抖、并发上限 |
| `services/copilot-core/src/copilot_core/prompt/{system_answer_v1.md,builder.py}` | 提示词模板与版本化 |
| `services/copilot-core/src/copilot_core/metrics/latency.py` | 单调时钟埋点、直方图 |
| `services/copilot-core/tests/llm/test_deepseek_direct.py` | 用本地 mock SSE 服务器（无 key 可跑） |
| `services/copilot-core/tests/llm/test_deepseek_e2e.py` | 真 API（无 key 自动 skip） |
| `tools/bench_latency.py` | 端到端压测：N 次、输出 P50/P95/分位 |

**任务**

1. `deepseek-direct`：`httpx.AsyncClient` + `stream=True`，读 SSE `data:` 行；连接复用；`max_tokens` 与超时按配置。
2. 提示词 v1：口语化、先结论、≤3 句、避免"根据您的问题"这类套话；固定版本号写入答案记录。
3. 上下文组装：最近 20 段、4000 字符上限、问题前置；KB 未接入时留空实现。
4. 取消：新问题到达即 `AbortController` 等价物；已结束的流不得再写库。
5. 延迟锚点：`anchorTs` = VAD 段尾；T1 = 首个 `text-delta`；T2 = `answer.end`。三者都写 `latency_sample`。
6. `bench_latency.py`：用 fixture 的 20 个问题，各跑 5 次 = 100 次，输出分布。
7. 密钥只从 Electron 握手传入；**禁止**从命令行/环境变量读取（测试除外）。

**验收标准**

- [ ] 端到端链路可跑：fixture 问题 → 悬浮窗（或 CLI）看到流式答案。
- [ ] `latency_sample` 完整记录 anchor/first-token/total，可导出 CSV。
- [ ] 100 次基准产出报告，**如实记录** P50/P95（无论是否达标）。
- [ ] 取消正确：连续 5 个问题时只有最后一个产生完整答案，前 4 个标记 `cancelled`。
- [ ] 无 key 环境下 e2e 自动 skip，mock 测试全绿。
- [ ] 错误路径：超时、401、429、空响应各有用例。

**测试命令** `[计划]`

```bash
uv run --project services/copilot-core pytest services/copilot-core/tests/llm -q
DEEPSEEK_API_KEY=... uv run --project services/copilot-core pytest services/copilot-core/tests/llm/test_deepseek_e2e.py -q
uv run --project services/copilot-core python tools/bench_latency.py --fixtures tests/fixtures/detect/labeled.jsonl --runs 100 --out .artifacts/latency-phase5.json
```

**退出条件**：`latency-phase5.json` 存在且被 `BUILD_LOG.md` 引用。**此时才第一次有资格讨论是否接近 1s 目标**；不达标属正常，转入 Phase 9。

---

## Phase 6 — Electron 悬浮窗

**目标**：不抢焦点、可点击穿透、不被会议共享捕获的常驻窗口；采集状态一眼可见。

**依赖**：Phase 3（能看到 partial 即可）；可与 Phase 5 并行。

**文件**

| 路径 | 内容 |
|---|---|
| `apps/desktop/src/main/index.ts` | 应用生命周期、单实例、IPC |
| `apps/desktop/src/main/windows/overlay.ts` | 透明/置顶/`setIgnoreMouseEvents`/`setContentProtection`/多显示器定位 |
| `apps/desktop/src/main/windows/settings.ts` | 设置窗口 |
| `apps/desktop/src/main/tray.ts` | 托盘菜单、状态图标 |
| `apps/desktop/src/main/secrets.ts` | `safeStorage` 加解密 |
| `apps/desktop/src/main/supervisor.ts` | 拉起/重启 Core 与 Audio |
| `apps/desktop/src/preload/{overlay,settings}.ts` | contextBridge 暴露最小 API |
| `apps/desktop/src/renderer/overlay/{App.vue,components/*}` | 状态点、计时、问题、答案流、错误横幅 |
| `apps/desktop/src/renderer/settings/*` | 设备、模式、隐私、快捷键 |
| `apps/desktop/tests/` | Playwright（Electron）冒烟 |

**任务**

1. 窗口：`transparent`、`alwaysOnTop('screen-saver')`、`skipTaskbar`、`focusable:false`、`setIgnoreMouseEvents(true, { forward: true })`；按住修饰键或点击托盘时临时可交互。
2. 内容保护：默认 `setContentProtection(true)`；设置里可关并告知风险。
3. 多显示器与 DPI：按 `screen.getDisplayNearestPoint` 定位；记住位置。
4. 状态可视化：采集中红点 + 计时；暂停灰点；错误红横幅（可点击看原因）。
5. 答案流式渲染：增量追加、自动滚动到结论、超长截断 + 展开。
6. 快捷键：全局 `Ctrl+Alt+R` 开关采集、`Ctrl+Alt+S` 停止（与会议软件冲突时可在设置改）。
7. IPC 白名单：preload 只暴露 `startCapture/stopCapture/onEvent/openSettings`，无任意 `send`。
8. 崩溃恢复：窗口 renderer 崩溃后重建并回放最近状态。

**验收标准**

- [ ] 窗口不抢焦点：会议中点击不会切走焦点（手动验证 + 自动化断言 `focusable=false`）。
- [ ] 点击穿透生效：鼠标可透过悬浮窗操作下方会议窗口。
- [ ] 腾讯会议屏幕共享时悬浮窗不可见（U7 实机验证，Win10 与 Win11 各一次）。
- [ ] 采集状态从点击到可见 < 300ms。
- [ ] renderer 崩溃后 2s 内恢复显示，历史答案保留。
- [ ] preload 暴露面通过白名单测试（尝试调用未暴露方法应失败）。
- [ ] 内存：静置 1 小时 RSS 增长 < 50MB。

**测试命令** `[计划]`

```bash
pnpm -C apps/desktop typecheck
pnpm -C apps/desktop test          # vitest 组件测试
pnpm -C apps/desktop test:e2e      # Playwright + Electron
pnpm -C apps/desktop dev           # 手动验证清单
```

---

## Phase 7 — Harness 集成

**目标**：`dsh` 里能看见会议内容、能查询、能按需注入；卸载后 dsh 完全不受影响。

**依赖**：Phase 5（需要真实答案事件）。

**文件**

| 路径 | 内容 |
|---|---|
| `packages/dsh-copilot-bridge/package.json` | peer 依赖、`dsh.bundle.patch` |
| `packages/dsh-copilot-bridge/cordis.patch.yml` | 一行 insert |
| `packages/dsh-copilot-bridge/src/index.ts` | 插件入口（name/inject/Config/apply） |
| `packages/dsh-copilot-bridge/src/client.ts` | WS 客户端、重连、请求关联 |
| `packages/dsh-copilot-bridge/src/tools.ts` | 三个只读工具 |
| `packages/dsh-copilot-bridge/src/inject.ts` | `agent/pre-step` 注入（默认关） |
| `packages/dsh-copilot-bridge/src/service.ts` | `ctx.copilot` 服务 |
| `packages/dsh-copilot-bridge/tests/*` | 单测 + Loader 组合测试 |
| `tools/verify-bridge-install.mjs` | 干净 Harness home 里安装并冒烟 |

**任务**

1. 用 `@deepseek-ai/dsh-tools` 的 `defineTool` 注册三个工具；参数 JSON Schema 与 `PLUGIN_API.md` 一致。
2. WS 客户端：读 `runtime.json`、`hello` 握手、请求-响应关联（`requestId`）、断线重连（1s/2s/5s）、消息上限。
3. `agent/pre-step` 注入：只在 `injectContext` 与在线时才挂；返回 `messages` 时保留 `{ ...decision }` 其他字段（waterfall 规则）。
4. `ctx.copilot` 服务与 `ctx.effect` 生命周期；卸载时关闭 socket。
5. 按 Harness 的测试规则写 **Loader 真实组合测试**（不是手搓 `ctx.plugin`）：boot 一个只含必要行的 `cordis.yml`，安装本插件，断言工具可见、注入内容进模型请求、卸载后无残留。
6. 兼容矩阵：`0.2.1-rc.2` 上安装；记录 `dsh plugin` 的实际解析结果（U9）。
7. （可选）H2：暴露本地回环端点让 Core 调 `ctx.llm.stream()`；先用一个最小实验验证可行性（U6），再决定是否进入 Phase 8 之后。

**验收标准**

- [ ] 干净 Harness home 中 `dsh plugin` 安装本 bundle 成功，`dsh --profile web --dump-config` 能看到 `copilot-bridge` 行。
- [ ] Core 未运行时：dsh 正常启动，工具返回 `copilot_unavailable`，无未捕获异常。
- [ ] Core 运行时：`copilot_transcript` 返回最近转写；`copilot_last_answer` 返回最近答案。
- [ ] 开启注入后，模型请求中能看到带 `source.kind='realtime-copilot'` 的 user message；关闭后无。
- [ ] 卸载插件后无残留服务、无 socket、无定时器（dispose 断言）。
- [ ] 不新增任何 `SessionEventMap` 成员（避免触发持久化格式契约）。
- [ ] U6 有明确结论并写回 `ARCHITECTURE.md`（H2 落地或放弃）。

**测试命令** `[计划]`

```bash
pnpm -C packages/dsh-copilot-bridge test
pnpm -C packages/dsh-copilot-bridge build
node tools/verify-bridge-install.mjs --home .artifacts/dsh-home
```

**风险**：R1、R3。若 peer 依赖在 `dsh plugin` 安装后解析出第二份单例，改为"零运行时依赖"实现（只用结构类型 + 从 `ctx` 取服务），并把结论写进 `PLUGIN_API.md`。

---

## Phase 8 — 独立运行

**目标**：不装 Harness 也能一键可用：supervisor 编排三进程、自启、设置、日志与诊断导出。

**依赖**：Phase 2/3/5/6。

**文件**

| 路径 | 内容 |
|---|---|
| `apps/desktop/src/main/supervisor.ts` | 端口分配、进程启动、健康检查、退避重启、优雅退出 |
| `apps/desktop/src/main/paths.ts` | `%APPDATA%` / `%LOCALAPPDATA%` 布局、日志轮转 |
| `apps/desktop/src/main/diagnostics.ts` | 一键导出（配置 + 日志 + 版本 + 无音频/无密钥） |
| `apps/desktop/src/main/autostart.ts` | 开机自启（默认关） |
| `apps/desktop/src/main/single-instance.ts` | 单实例锁、二次启动唤出 |
| `tools/smoke-standalone.ps1` | 端到端冒烟脚本 |

**任务**

1. 启动顺序：UI → Core（等 `/healthz`）→ Audio（等 `audio.ready`）；任何一步失败给出可操作错误。
2. 端口：Core 用 0 端口，写 `runtime.json`（原子替换 + `0600`）；UI 读取后连接。
3. 监督：退出码/心跳双重判定；重启上限与退避；连续 5 次失败转"安全模式"（只开 UI + 诊断）。
4. 优雅退出：先停采集，再停 Core，最后退出 UI；等待各进程最多 5s。
5. 首次运行引导：选择设备、填 API key（仅独立模式）、隐私说明、快捷键。
6. 诊断包：不含音频、不含 key、不含转写正文（只含长度与哈希）。
7. 冒烟脚本：启动 → 采集 60s 播放 fixture → 检查答案 ≥1 条 → 退出 → 断言无残留进程。

**验收标准**

- [ ] 双击启动到可采集 < 4s（冷启动，SSD）。
- [ ] 三进程全部崩溃可各自恢复；同时崩溃按顺序重启。
- [ ] 退出后无残留进程、无占用端口、`runtime.json` 被清理。
- [ ] 首次运行引导可完成且可跳过（跳过则采集不可用并给出原因）。
- [ ] 诊断包不含 PII（脚本扫描 key 与转写正文）。
- [ ] 开机自启默认关闭；开启后登录可自动启动但**不自动开始采集**。

**测试命令** `[计划]`

```bash
pwsh -File tools/smoke-standalone.ps1 -Duration 60
pnpm -C apps/desktop test:e2e
```

---

## Phase 9 — 性能优化

**目标**：把 P50/P95 压到目标区间，或给出有数据支撑的替代承诺。

**依赖**：Phase 5（有基线）、Phase 6（有真实渲染链路）。

**文件**

| 路径 | 内容 |
|---|---|
| `tools/bench_latency.py` | 已有；增加 `--stage` 分解与 `--warmup` |
| `services/copilot-core/src/copilot_core/metrics/report.py` | 分位报表、分阶段瀑布 |
| `services/copilot-core/src/copilot_core/pipeline/answer.py` | 并发与预取策略 |
| `apps/desktop/src/renderer/overlay/*` | 首帧渲染优化（避免重排） |
| `benchmarks/README.md` | 基准方法与机器规格（对齐 Harness 的 bench lane 思路） |

**任务**

1. 阶段分解：采集→VAD→ASR→检测→KB→组 prompt→TTFB→渲染，每段直方图。
2. 连接预热：启动即建立 DeepSeek TLS 连接并保活；Core 空闲时定时 ping。
3. 提示词瘦身：上下文从 4000 字符降到 2000（可配置），测质量-延迟曲线。
4. partial 提前触发 A/B（U10）：final 复核不一致时取消重发，验证不产生双答案。
5. 渲染优化：答案增量追加用 `requestAnimationFrame` 节流；避免整段重排。
6. 并发控制：`max_concurrent=2`；超出的问题排队并标记。
7. 出报告：`latency-phase9.json` + 人读版 `PERF.md`（放在 `.artifacts/`，不入库大数据）。

**验收标准**

- [ ] 参考机（记录 CPU/网络规格）上 N=100：**P50 < 1000ms 且 P95 < 2000ms**，按锚点定义测量。
- [ ] 分阶段瀑布可解释总延迟（各项之和与总延迟差 < 15%）。
- [ ] 优化前后对比表，每项改动注明"改了什么都、影响多少"。
- [ ] 不达标的阶段有明确结论：是 ASR 定稿、还是 LLM TTFB，还是本机调度。
- [ ] 若未达标，`README.md` 与对外文案改为实测数字，**禁止**保留 1s/2s 的宣称。

**测试命令** `[计划]`

```bash
uv run --project services/copilot-core python tools/bench_latency.py --runs 100 --warmup 5 --stage all --out .artifacts/latency-phase9.json
uv run --project services/copilot-core python -m copilot_core.metrics.report .artifacts/latency-phase9.json
```

---

## Phase 10 — Windows 打包

**目标**：可签名、可升级、可卸载的安装包；干净机器可用。

**依赖**：Phase 8/9。

**文件**

| 路径 | 内容 |
|---|---|
| `apps/desktop/electron-builder.yml` | win x64、NSIS、图标、协议、`extraResources`（audio sidecar + python core） |
| `scripts/prepare-runtime.ps1` | 收集 sidecar 产物与 Python onedir 运行时 |
| `scripts/sign.ps1` | 签名（EV/OV）、时间戳、校验 |
| `apps/desktop/scripts/test-installer.mjs` | 安装→启动→冒烟→卸载 |
| `.github/workflows/release.yml` | 构建、签名、发布、校验和 |

**任务**

1. Core 打包：PyInstaller `onedir`（非 onefile，冷启动更快）；剔除 tests、缓存。
2. sidecar：`dotnet publish -r win-x64 --self-contained false`（依赖 .NET Desktop Runtime 需在安装器检测，或改成 self-contained 并接受体积）。
3. 全部资源进 `extraResources`，运行时从 `process.resourcesPath` 解析。
4. NSIS 安装器：每用户安装（免管理员）、开始菜单、卸载时清理 `%APPDATA%`（询问）。
5. 签名与 SmartScreen：EV 证书优先；记录未签名时的用户提示。
6. 自动更新：`electron-updater` 指向自有发布源；更新前后检查进程状态（采集中的会话要先停）。
7. 干净 Windows 10 与 11 各一台 VM 验证；记录杀软误报。

**验收标准**

- [ ] 干净 Win10 22H2 与 Win11 上安装、启动、采集、出答案全流程通过。
- [ ] 安装包 ≤ 180MB；冷启动 ≤ 5s。
- [ ] 签名验证通过（`signtool verify /pa`），安装器无 SmartScreen 拦截（有证书时）。
- [ ] 卸载后无残留进程、无残留服务、可选清理用户数据。
- [ ] 升级：旧版本运行中升级被阻止并提示；升级后设置与 KB 保留。
- [ ] 安装器测试脚本在 CI 可跑（Windows runner）。

**测试命令** `[计划]`

```bash
pnpm -C apps/desktop build
node apps/desktop/scripts/test-installer.mjs --artifact .artifacts/RealTimeCopilot-Setup.exe
signtool verify /pa /v .artifacts/RealTimeCopilot-Setup.exe
```

---

## 全局完成定义（每个阶段都适用）

1. 验收清单全部勾选，证据（日志/报告/截图）链接进 `BUILD_LOG.md`。
2. 新增/变更的接口先改 `PLUGIN_API.md`，再改代码。
3. 测试覆盖新增逻辑；性能相关改动附前后数据。
4. 不引入密钥、音频样本、真实会议内容进仓库。
5. 不修改 DeepSeek Harness 仓库源码；bridge 通过 peer 依赖与公开插件契约工作。
6. 已知限制写进对应文档的"限制"小节，而不是留在口头。
