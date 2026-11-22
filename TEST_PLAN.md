# 测试与测量计划

本文定义"什么算通过"。任何阶段宣称完成，都必须能指向本文中的具体门禁。
当前**没有任何测试代码**，全部命令标注 `[计划]`，在对应阶段建立后才可执行。

---

## 1. 分层与归属

| 层 | 覆盖对象 | 工具 | 运行时机 |
|---|---|---|---|
| L1 单元 | 纯函数：分帧、重采样、去重、启发式打分、配置校验、协议编解码 | pytest / dotnet xunit / vitest | 每次提交 |
| L2 契约 | Provider 接口不变量、事件 schema 往返、配置模式 | pytest（含 fake provider）+ JSON Schema 校验 | 每次提交 |
| L3 集成 | Core + fake ASR/LLM（本地 mock SSE）；sidecar + 假设备（WAV 回放） | pytest + 本地服务器 | 每次提交 |
| L4 端到端 | 真 Core + 真 sidecar + 真 UI（无 key 用 mock 模型；有 key 用真模型） | Playwright(Electron) + 冒烟脚本 | 合并前 / 夜间 |
| L5 性能 | 锚点→首 token 分布、分阶段瀑布 | 自建 bench 工具 | 阶段门禁 + 夜间 |
| L6 兼容 | 指定 Harness 版本上的安装与运行 | Loader 组合测试 + 干净 home 安装 | 每次 bridge 变更 |
| L7 安全 | 鉴权、绑定地址、密钥、隐私、注入面 | pytest + 静态扫描 + 手工清单 | 每次提交（静态）+ 阶段门禁（手工） |

**原则**（沿用 Harness 的测试哲学）：

- 只 mock 昂贵或非确定的边界（ASR 供应商、LLM、时钟、设备），下游保持真实。
- 断言"世界"，不断言自述：验证 Core 真的发出了字节、SQLite 真的写了行、进程真的退出了。
- 每个注册都要有 dispose 断言（插件/服务/socket/定时器）。
- 测试自己拥有资源：端口、临时目录、子进程，在 teardown 释放，失败也释放。

---

## 2. 仓库与命令

```text
realtime-copilot/
├─ services/copilot-core/tests/     # pytest
├─ services/audio-capture/tests/    # dotnet test
├─ packages/protocol/tests/         # vitest
├─ packages/dsh-copilot-bridge/tests/ # vitest + Loader 组合
├─ apps/desktop/tests/              # vitest（组件） + Playwright（Electron）
└─ tools/                           # bench、fixture、诊断
```

| 目标 | 命令 `[计划]` |
|---|---|
| Core 单测 | `uv run --project services/copilot-core pytest services/copilot-core/tests -q` |
| Core 覆盖率 | `uv run --project services/copilot-core pytest --cov=copilot_core --cov-fail-under=80` |
| sidecar | `dotnet test services/audio-capture/tests -c Release` |
| 协议 | `pnpm -C packages/protocol test` |
| bridge | `pnpm -C packages/dsh-copilot-bridge test` |
| UI 组件 | `pnpm -C apps/desktop test` |
| UI e2e | `pnpm -C apps/desktop test:e2e` |
| 端到端冒烟 | `pwsh -File tools/smoke-standalone.ps1 -Duration 60` |
| 延迟基准 | `uv run --project services/copilot-core python tools/bench_latency.py --runs 100 --warmup 5` |
| 全量 | `pnpm -C apps/desktop run verify:all`（聚合上述，Phase 8 建立） |

覆盖率门槛：Core ≥ 80%（行），协议层 100%（它是契约），bridge 的关键路径 100%（工具注册、注入、卸载），UI 组件不设硬门槛但关键状态（采集指示、错误横幅）必须有测试。

---

## 3. 测试数据与确定性

- **音频 fixture**：`tests/fixtures/audio/*.wav`，16k mono，每条 30–60s，附人工转写 `.txt`。来源：自制朗读 + 公开许可素材；**不得**使用真实会议录音。
- **问题标注集**：`tests/fixtures/detect/labeled.jsonl`，字段 `{text, is_question, normalized_text, notes}`，≥80 条，正负各半。
- **假 ASR**：按 fixture 的参考文本按时间片吐 partial/final；可注入延迟、乱序、断流。
- **假 LLM**：本地 HTTP SSE 服务器，支持可控 TTFB、分块大小、错误码、慢速/挂起。
- **时钟**：延迟测试用 `time.perf_counter_ns()`（进程内单调）；跨进程用 QPC 时间戳并做偏移校准（sidecar 与 Core 同机，偏移 < 1ms）。
- **禁止**：在测试里 sleep 等待真实网络；用可注入的 clock 与 code。

---

## 4. 延迟测量协议（目标判定的唯一口径）

**这是全文最重要的部分。任何 P50/P95 数字若不符合本协议，不得对外使用。**

### 4.1 定义

- **锚点 T0**：`anchorTs` = VAD 判定 `speech_end` 时**最后一帧有效语音**的采集时间戳（`captureTsNs`）。
  不是判定时刻，不是 ASR 返回时刻，不是 Core 收到 final 的时刻。
- **T1**：第一个 `answer.delta` 被 Core 写入事件总线的时刻。
- **T2**：`answer.end` 的时刻。
- **报告指标**：`T1 - T0`（首答延迟），`T2 - T0`（完整答案延迟），单位 ms。
- 同时报告从"ASR final 进入系统"到 T1 的次级指标，用于区分 ASR 与 LLM 的贡献；但**主指标必须是 T0 起算**。

### 4.2 采样规则

- N ≥ 100 次有效样本（排除：用户手动取消、网络断开、无问题的输入）。
- 前 5 次为 warmup，不计入。
- 每次使用不同问题（来自标注集），避免缓存命中导致失真；同时单独报告"重复问题"的分布以体现缓存收益。
- 记录每次的：问题文本哈希、ASR 供应商、模型、`max_tokens`、网络类型、是否命中 KB、是否触发 LLM 复核。

### 4.3 环境记录（缺一不可）

CPU 型号与核数、内存、Windows 版本与 build、是否插电/节能模式、网络类型与运营商、DeepSeek 端点、ASR 供应商与区域、Core/sidecar/UI 版本、Harness 版本（若在 Harness 模式）。

### 4.4 判定

- **达标**：P50 < 1000ms 且 P95 < 2000ms。
- **分阶段解释**：采集/VAD/ASR 定稿/检测/KB/组包/TTFB/渲染，每一项给出 P50/P95，且总和与总延迟差 < 15%（否则说明埋点遗漏）。
- **不达标时的动作**：按 `ARCHITECTURE.md §5` 的降级顺序处理；对外文案改为实测值。

### 4.5 已知陷阱（写下来是为了不再犯）

1. 用"ASR final 到达"当锚点 → 系统性隐藏 200–400ms，属自欺。
2. 只测一次或只测 P50 → 会掩盖长尾；必须报 P95。
3. 在 mock LLM 上测出的数字不能代表真实 TTFB；必须区分并分别标注。
4. 关闭 UI 渲染只测 Core → 用户感知的是屏幕上的字，必须包含渲染。
5. 复用同一问题导致 KV/cache 命中 → 高估；必须用不同问题，并单独报告重复场景。
6. 节能模式/电池供电下测 → 结果不可比；必须记录电源状态。

---

## 5. 音频帧通道测试（Phase 2-A）

协议定义见 `packages/protocol/audio-frame.v1.md`。三层覆盖：

| 层 | 内容 | 位置 |
|---|---|---|
| 结构 | 头部字段、字节序、长度、错误码；跨语言 fixtures 往返 | `tests/test_audio_frame.py`、`tests/AudioFrameCodecTests.cs` |
| 会话 | 序号连续性/回绕/重复、时间戳回退、速率限制、断开阈值 | `tests/test_audio_session.py`、`tests/AudioStreamerTests.cs` |
| 通道 | 真实 uvicorn + 真实 WS 客户端：接收、丢帧、超限、角色拒绝、会话释放、日志不泄露 | `tests/test_ws_audio.py` |

**跨语言一致性**：Python 与 C# 读取同一份 `fixtures/audio/*.bin`，错误码字符串必须完全一致（`unsupported_version`、`invalid_sample_rate`、`payload_length_mismatch` 等）。C# 用 `SimulatedAudioSource` 生成 seq=1、ts=1e9 的帧，序列化结果必须与 `valid-frame.bin` 逐字节相同。

**压力与稳定性**：

- 单元层：令牌桶在 200fps 配置下允许 250 突发，超出即计数并请求断开。
- 通道层：400 帧瞬时洪泛触发 `rate_limited` 并断开；30 个无效帧触发 `invalid_payload` 断开。
- 端到端：`scripts/e2e-audio.ps1 -Seconds 60` 以 50fps 连续发送 60 秒，核对帧数、字节数、丢帧、无效帧、重复帧与会话释放；CI 的 Windows 作业执行同一命令。

**判定标准**（当前实测值见 BUILD_LOG）：

- Core 收到帧数 == 客户端发出帧数，误差为 0；
- `gaps` == 主动跳过的帧数（末帧被跳过时允许少 1）；
- `invalid` / `duplicates` / `rateLimited` 均为 0；
- 连接结束后 `sessions` 回到 0，统计不清零。

### 真实采集（Phase 2-B）

| 层 | 内容 | 位置 |
|---|---|---|
| 纯逻辑 | 浮点→PCM16 限幅与字节序、跨回调分帧、时间戳按帧推进 | `tests/Pcm16Tests.cs`、`tests/AudioFramerTests.cs` |
| 管线 | 采集事件→队列→发送；采集失败与发送失败的传播；停止时释放设备 | `tests/FrameStreamerTests.cs` |
| 参数 | `--capture-loopback` / `--capture-mic` / 设备 ID 解析 | `tests/CaptureOptionsTests.cs` |
| 真实设备 | 播放测试音 → loopback 采集 → 帧数与整段峰值（> -45 dBFS） | `scripts/capture-smoke.ps1` |

**硬件与 CI**：真实采集依赖音频端点，GitHub 托管的 Windows runner 没有可用输出设备，因此**不进 CI**；CI 继续跑模拟音频的 60 秒端到端。真实采集的证据是本机 `capture-smoke.ps1` 的输出，记录在 `BUILD_LOG.md`。没有端点时脚本输出 SKIP 并以 0 退出，不伪装成通过。

**已知环境差异**：用 `WithDevice(默认端点)` 打开 loopback 时，本机曾出现其他进程播放被采成静音；改为让 builder 解析默认端点，或在修复枚举器生命周期后显式指定端点，均正常。跨机器行为需在目标机上用同一脚本复测。

### 采集控制面（Phase 2-C）

| 层 | 内容 | 位置 |
|---|---|---|
| 状态机 | 合法迁移、非法迁移、错误态复位、迁移表完整性 | 	ests/test_capture_state.py |
| 票据 | 一次性、重放、过期、跨会话、篡改、无会话签发 | 	ests/test_tickets.py |
| 控制面 | 幂等 start、启动/停止超时、sidecar 退出、断连、idle 停止、错误后重启、票据不落日志 | 	ests/test_capture_controller.py |
| 协议 | audio.status 字段、hello ticket 序列化、配置读取 | 	ests/ControlProtocolTests.cs、	ests/AppConfigTests.cs |
| 端到端 | 真实 Core + 真实 sidecar：start→capturing→帧→stop→idle，无残留进程 | scripts/e2e-control.ps1、scripts/control_client.py |
| 三态判定 | PASS/SKIP/FAIL 与错误码（no_audio_device / playback_failed / capture_failed / no_frames / invalid_frames / silent_capture） | scripts/capture-smoke.ps1 |
| 稳定性 | 30 分钟采样 CPU/WorkingSet/PrivateBytes/帧率/丢帧/状态，输出 JSON+CSV | scripts/capture-stability.ps1 |

### 控制面加固（P2-C.1）

| 类别 | 断言 |
|---|---|
| 并发 | 20 个并发 start 只 spawn 一个进程；20 个并发 stop 只发送一次 `capture.stop` 且只终止一次；start/stop 交错不抛 `InvalidTransition`、同一时刻最多一个存活进程 |
| 迟到事件 | STARTING + idle → ERROR(`sidecar_idle`) 并清理；STOPPING + capturing 被忽略，随后 idle 回 IDLE；ERROR 不被迟到 capturing/idle 清除；旧连接/wrong requestId 的状态被忽略 |
| 清理副作用 | 超时/错误/断连/关闭后：进程被终止一次、票据失效（`consume` 返回非 ok）、超时与监听任务结束、`sidecarPid` 为 null |
| 崩溃与停止竞态 | sidecar 崩溃与用户 stop 同时发生时不抛异常、终止次数 ≤ 1、最终状态为 IDLE 或 ERROR |

这些测试断言真实副作用（spawn 次数、`terminate_calls`、票据有效性、后台 Task、连接引用），不只比较状态字符串。

### 控制面最终加固（P2-C.2）

| 类别 | 断言 |
|---|---|
| spawn 窗口 | 锁外创建进程：stop/shutdown 在 spawn 期间发生时，新进程被立即终止；spawn 完成时会话已失效则不登记为当前会话 |
| 任务归属 | 每个会话持有 timeout/watch/control 任务；旧会话任务在清理时结束且不影响新会话任务；shutdown 取消全部会话任务；不取消当前任务 |
| disconnect 竞态 | 同一锁内取会话/状态快照；capturing 意外断连进 ERROR，stopping 断连按正常结束回 IDLE；disconnect 与 stop 并发不误报异常；disconnect 与进程退出并发只终止一次 |
| 票据边界 | 到期瞬间即失效；到期前 1 秒仍有效；超长输入（>512）、非法 Base64、非法 JSON、旧/已结束会话、重放均拒绝且不泄露签名或 secret |
| 失败清理 | spawn 失败进入 ERROR(sidecar_unavailable)，无进程、无票据、无残留任务 |

**CI 限制**：托管 Windows runner 没有音频端点，控制面 E2E 在 CI 用 -Simulated（sidecar 用静音帧源）
验证真实的跨进程控制与二进制帧链路；真实 WASAPI 采集的 E2E 只能在本机运行，结果记录在 BUILD_LOG.md。

## 5A. ASR 协议与接口准备（P3-A/B/C/D）

| 类别 | 断言 |
|---|---|
| Schema/fixtures | `asr.status` / `asr.partial` / `asr.final` / `asr.error` 的合法 fixture 通过 `parse_event`；Python 与 TS 生成类型同步 |
| 流式约束 | partial revision 必须递增；final 后拒绝同段 partial/final；final revision 不早于最近 partial；时间戳倒退与 start>end 拒绝 |
| 状态分离 | `AsrStatusTracker` 进入 error 时采集状态仍为 CAPTURING、采集错误码为空 |
| 票据用途 | audio 票据不能当 asr 用；asr 票据必须带 generation；过期/重放/旧会话/结束会话拒绝；payload 不含密钥 |
| VAD | 能量阈值、speech_started/ongoing/ended、最短语音、Ring Buffer 上限与丢弃计数、loopback/mic 独立、时间戳单调 |

### P3.1 运行时接线

| 类别 | 断言 |
|---|---|
| 片段 | pre-roll 与起始确认帧纳入；结束时间取最后有效语音；结束静音单独记录；超长切分 |
| Ring Buffer | 有界、覆盖计数；loopback/mic 独立 |
| ASR 队列 | 帧队列与 Segment 队列均有界；满时计数丢弃；stop 时 flush；VAD 异常不断采集 |
| 真实 PCM | `fixtures/vad-sample.wav` 合成音频产出片段，PCM 与输入一致 |

### P3.1.1 加固

| 类别 | 断言 |
|---|---|
| PCM 口径 | peak/RMS 基于最终 PCM；结束静音不计入；`endSilenceMs` 单独记录 |
| Energy VAD | Peak+RMS、噪声底 EMA、自适应阈值、起止滞回、噪声触发计数 |
| Provider | `EnergyVadProvider` 包装；`WebRtcVadProvider` 缺依赖时 `VadUnavailable` |
| PCM 预算 | 单段/全局预算超限丢弃并计数；消费释放；close 清空 |
| 生命周期 | start/stop_graceful/stop_immediate/close 幂等、无 Task 残留、worker 异常可观察 |

### P3.2 ASR 运行时

| 类别 | 断言 |
|---|---|
| 启动/票据 | purpose=asr、绑定 generation、一次性；旧 generation 拒绝启动 |
| 结果校验 | revision 单调、final 后拒绝旧 partial、同段只 final 一次、冲突广播 asr.error |
| 生命周期 | 连接失败/认证失败/超时/取消/停止；停止后迟到结果不广播 |
| 双来源 | loopback 与 mic 各自串行消费，互不阻塞 |
| 广播 | asr.status/partial/final/error 经 Core broadcast；Provider 不直接操作 WebSocket |

说明：真实 Provider 使用 Mock 测试；Deepgram 未验证（NOT_VERIFIED）。

## 5B. 音频可靠性（P2-D）

| 类别 | 断言 |
|---|---|
| 按来源统计 | loopback 与 mic 的帧数/丢帧/重复/峰值/RMS/静音时长/时间戳/采样率/声道完全独立；`/healthz.audio.sources` 与 `audio.status.sources` 同源 |
| 静音时长 | 静音帧累加 `silenceDurationMs`，出现语音后归零 |
| C# Channel | 容量 200 有界；消费者阻塞时满载丢帧且计数；停止后重复 Emit 不崩溃；取消后源恰好 Stop 一次 |
| 稳定性报告 | 报告绑定 Git SHA、sidecar 版本、模式、起止时间、理论/实际帧数、状态迁移、sidecar 退出码；未完成 30 分钟或 start/stop 失败时退出码非零；真实模式无设备时标记 SKIP |

**未覆盖**：设备热插拔自动恢复、进程级 loopback 的实机验证（NOT_VERIFIED）。

### P2-D.1 补充

| 类别 | 断言 |
|---|---|
| 恢复策略 | `RecoveryPolicy` 退避 500ms/1s/2s、3 次上限、越界钳制 |
| FrameStreamer 生命周期 | 启动前失败、发送失败、Stop 与回调竞争、重复 Stop/Dispose、多线程回调计数一致 |
| 稳定性可信度 | 脏工作区输出 `result=NOT_REPRODUCIBLE`（不 PASS）；报告含 SHA/哈希/模式/构建配置 |
| 进程 loopback 探测 | Build ≥20348、API 存在性、PID 有效性；目标应用一律 NOT_VERIFIED |

## 6. 安全测试

| 编号 | 断言 | 方式 |
|---|---|---|
| S1 | 所有监听 socket 绑定 `127.0.0.1` | 启动后 `netstat -ano`/`Get-NetTCPConnection` 断言；代码层配置校验拒绝非回环地址 |
| S2 | 无 token 无法建立可用连接 | 集成测试：缺失/错误/超时三种 hello |
| S3 | 密钥不落明文 | 扫描 `%APPDATA%` 下所有文件不含 key 前缀；`runtime.json` 只含 hash/token |
| S4 | 日志不含 key、不含转写正文 | 日志模板断言 + 静态规则（禁止 `log.*text`） |
| S5 | 原始音频默认不落盘 | 采集 10 分钟后扫描目录无新增音频文件 |
| S6 | 采集必须用户手势触发 | 代码审查 + 测试：无 UI 交互时不存在自动 `capture.start` |
| S7 | 采集状态始终可见 | UI 测试：任意状态变化后指示器存在且文案正确 |
| S8 | 无输入注入能力 | 依赖扫描：`package.json`/`*.csproj` 不含 `robotjs`/`nut.js`/`Win32 SendInput` 封装；代码 grep 无 `SendInput`/`keybd_event` |
| S9 | 悬浮窗内容保护 | 手工：腾讯会议共享屏幕时不可见；设置关闭时有明确警告 |
| S10 | 注入 Harness 需显式开启且告知 | 测试：默认配置下不产生 `realtime-copilot` source 消息 |
| S11 | 卸载插件无残留 | dispose 断言：socket 关闭、定时器清空、服务移除 |
| S12 | 诊断包不含隐私 | 脚本解包并扫描 key/转写文本关键字 |
| S13 | 只有 `role=audio` 能发音频帧 | 集成测试：`ui` 角色发送二进制帧收到 `invalid_role` 并断开 |
| S14 | 音频帧不落盘、不进日志 | 集成测试断言应用日志不含 token / PCM / payload；仓库中无音频写入代码路径 |
| S15 | 洪泛与超大帧被拒绝 | 400 帧瞬时洪泛 → `rate_limited` 断开；5000 字节消息 → `invalid_payload` + 1009 |
| S16 | 音频角色不能下发命令 | 集成测试：`role=audio` 发 `capture.start` 收到 `invalid_role`，且音频流不断 |

---

## 7. Harness 兼容矩阵

| bridge 版本 | Harness 版本 | 状态 | 验证内容 |
|---|---|---|---|
| 0.1.x | 0.2.1-rc.2 | 目标 | 安装、加载、工具、注入、卸载 |
| 0.1.x | 0.2.x（后续 rc） | 待测 | 同上 + peer 解析 |
| 0.1.x | 0.3.x | 未知 | 需重跑；不承诺 |

验证步骤（每个矩阵格）：

1. 干净 `DSH_HOME`，`dsh plugin` 安装 bundle。
2. `dsh --profile web --dump-config` 包含 `copilot-bridge` 行且 config 正确。
3. 启动 `dsh web`，Core 未运行 → 工具返回 `copilot_unavailable`，进程不崩。
4. 启动 Core → 工具返回数据；开启注入后会话里出现 `realtime-copilot` 来源消息。
5. 禁用/卸载 → 无残留；`dsh` 功能不受影响。

---

## 8. 阶段门禁汇总

| 阶段 | 必须绿的门禁 |
|---|---|
| P1 | L1+L2 全绿；启动/配置/鉴权测试；三语言 CI |
| P2-A | 音频帧单测 + 会话测试 + 真实通道测试；60 秒端到端（帧数/丢帧一致）；角色限制 |
| P2-B | sidecar 真实采集单测；30 分钟稳定性；三场景实机记录；S1/S5；S6/S7 |
| P3 | ASR 契约测试；WER 与定稿延迟报告；断流恢复 |
| P4 | 检测评测报告；误报清单；性能 P95 |
| P5 | mock + 真 key 测试；**首份端到端延迟报告**；取消正确性 |
| P6 | UI 组件 + e2e；焦点/穿透/内容保护；S6/S7/S9 |
| P7 | Loader 组合测试；干净 home 安装；S11；兼容矩阵首行 |
| P8 | 冒烟脚本；崩溃恢复；S1/S3/S12 |
| P9 | N=100 延迟判定；分阶段瀑布；对比表 |
| P10 | 双系统安装；签名；升级；卸载残留 |

---

## 9. CI 分层

| lane | 触发 | 内容 | 目标时长 |
|---|---|---|---|
| `pr` | 每次 PR | L1+L2+L3（无 key），静态安全扫描 | < 6 分钟 |
| `nightly` | 每日 | L4（mock 模型）+ L5 基准 20 次 + L6 | < 30 分钟 |
| `release` | 打 tag | L4（真 key，人工触发）+ L5 N=100 + 打包 + 安装器 | 人工确认 |

Windows 专属测试（L2 的 WASAPI 部分、L4 的 UI）只在 Windows runner 跑；Linux CI 跳过并标记 `skipped`，不得用 mock 假装通过。

---

## 10. 明确不测试的内容（及其理由）

- **真实会议软件的音频路径**：无法在 CI 自动化，只能实机记录（Phase 2 清单），结论写入 `BUILD_LOG.md`。
- **DeepSeek 服务的 SLA**：不在我们控制内；只测我们自己的超时与降级。
- **杀软误报**：不可自动化，Phase 10 人工记录。
- **多显示器 DPI 组合**：抽样测试常见组合（100%/125%/150% + 双屏），不做全排列。

---

## 11. 回归清单（每次合并前人工过一遍）

1. 采集开关是否需要两次点击才能生效？
2. 停止采集后悬浮窗是否仍显示"采集中"？
3. 会议共享屏幕时是否看得到悬浮窗？
4. 断网 10 秒后答案是否恢复？
5. 连续 5 个问题是否只有最后一个有完整答案？
6. 退出应用后任务管理器是否有残留进程？
7. 日志里是否出现密钥或转写正文？


### P3.2.3 生命周期与 Deepgram 可靠性

| 类别 | 断言 |
|---|---|
| 生命周期 | Start 阻塞时 Stop、Stop 后旧 Start 自毁、重复 Stop、shutdown 取消并 join |
| Generation | 旧会话迟到的 Partial/Final 不广播 |
| Provider 状态 | 只有验证连接成功才 READY；认证失败不重试；429/5xx 有限退避 |
| URL | 标准编码；未知/空参数明确报错 |
| Drain | 迟到有效文本累积；Metadata/关闭/超时结束；取消关闭 socket |
| 指标 | 连接/重连/限流/超时计数与 activeWebSockets |

30/60 分钟：**NOT_RUN**。


### P3.2.9 ASR 统计

| 类别 | 断言 |
|---|---|
| 错误计数 | 单次/连续/不同错误码各计一次；snapshot 与内部一致；广播失败仍计数 |
| bySource | loopback/mic 的 partial/final 分别计数 |
| Provider 指标 | 失败连接不计成功；验证/取消/超时后 active sockets 归零；重连只计重试 |
| 延迟口径 | 只统计 Segment 内 first partial/final，不是端到端回答延迟 |


### P3.3 连接复用

| 类别 | 断言 |
|---|---|
| 复用 | 100 Segment 单连接；task_id 唯一；finish 前不开下一任务 |
| 隔离 | loopback/mic 各自连接；同来源串行 |
| 失败 | 失败废弃连接；无结果时重试一次；有 partial 不重放 |
| 生命周期 | 空闲重建；cancel/close 幂等；active sockets 归零 |
| 稳定性 | 30 分钟单来源、60 分钟双来源 PASS（TTS） |


### P34-04 transcript 协议

| 类别 | 断言 |
|---|---|
| Schema | `transcript.segment/reset` 通过 JSON Schema；fixtures 覆盖全部 EVENT_TYPES |
| Handler | 仅处理 asr.final；重复只一次；stop flush + reset；广播失败仍落库 |
| 运行时 | capture 生命周期接入在 P34-05 验证 |


### P34-05 生命周期

| 类别 | 断言 |
|---|---|
| capture start | 建 `sess_*` 会话并启动 ASR；final 落库 |
| generation | 新 generation 结束旧会话，旧会话 status=completed |
| capture stop | flush + 结束会话；ASR stop 失败也执行 |
| shutdown | 活动会话被结束；Handler 幂等 |
| 隔离 | 转写异常不影响 ASR 广播/采集 |


### P34-06 指标

| 类别 | 断言 |
|---|---|
| snapshot | assembled/persisted/持久化错误/广播错误、装配与缓冲计数 |
| 隐私 | snapshot 不含转写正文 |
| healthz | `/healthz.transcript` 反映 active/generation/persisted |
