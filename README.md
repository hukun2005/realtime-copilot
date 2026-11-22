# RealTime Copilot

Windows 实时 AI 会议助手：采集电脑输出音频（可选麦克风）→ 流式语音识别 → 识别问题 → DeepSeek 流式生成口语化答案 → 桌面悬浮窗展示。支持 **独立运行** 与 **DeepSeek Harness 集成** 两种模式。

## 当前状态

**Phase 1、Phase 2-A（音频协议）、Phase 2-B（WASAPI 采集）、Phase 2-C（采集控制面与安全）、P2-C.1 / P2-C.2（控制面加固）以及 P3 协议/票据/VAD 准备已完成并验证。** 当前是可运行、可编译、可测试的跨语言工程：

- 统一协议（JSON Schema → TypeScript / Pydantic / 手写 C# DTO）
- **音频二进制帧协议 v1**：28 字节头 + 640 字节 PCM16LE，跨语言 fixtures 与错误码一致
- Python Core：WebSocket 鉴权、心跳、错误模型、TOML 配置、SQLite 迁移、JSON 日志、**音频帧解析/序号连续性/速率限制/统计/电平**、**采集控制状态机（capture.start/stop、超时、崩溃处理、短期票据）**
- C# 音频进程：连接 Core、握手、心跳、断线识别、**二进制帧发送**、**WASAPI loopback/麦克风采集（48k 立体声 → 16k 单声道 → 20ms 分帧）**、**控制模式（等待 capture.start、上报 audio.status、设备变化检测）**、模拟音频流
- Electron + Vue3 桌面骨架：状态展示、IPC 白名单、Core 连接
- GitHub Actions 四作业 CI（含 Windows 真实 60 秒音频端到端）

## 当前能力状态

| 能力 | 状态 |
|---|---|
| WASAPI loopback / mic 采集、二进制帧 v1 | 已实现 |
| 设备恢复、稳定性报告 | 已实现；真实热插拔 **NOT_VERIFIED** |
| VAD（Energy VAD + 完整 PCM 片段） | 已实现；真实噪声评测 **NOT_VERIFIED** |
| 阿里云 Paraformer 实时 ASR | 已接入；真实 API **PASS**（TTS 测试音频，非会议） |
| WebSocket 连接复用 | **已实现**（P3.3；单连接顺序 run-task/finish-task） |
| Segment 顺序识别 / Partial / Final | **已实现**（每来源串行；partial 仅 UI 临时文本） |
| 持续实时流式 ASR（逐帧不切段） | **尚未实现**（后续项，不等同于连接复用） |
| 真人会议识别质量 | **NOT_MEASURED**（仅有 TTS 评测） |
| Deepgram Provider（Segment 级） | 代码已实现；真实连接 **NOT_VERIFIED**（无 API Key） |
| ASR 运行时（Manager/协调器/广播） | 已实现，Mock 测试通过 |
| 转写装配 / 缓冲 / SQLite 持久化 | **已实现**（P3.4；真实 WASAPI + Paraformer 2 分钟链路 PASS） |
| 问题检测引擎（P4） | **已实现**：规则检测/分类/追问/状态机；人工标注集评测通过，非真实会议 |
| LLM Review / 问答 | DeepSeek 真实 Review/非流式 Answer PASS；**流式 SSE + 取消已实现**；未接 UI/协议 |
| 30 分钟 ASR 联合测试 | **PASS**（真实 WASAPI + 连接复用 + 转写落库；TTS 音频） |
| 60 分钟 ASR 联合测试 | **PASS**（双来源 + 连接复用 + 转写落库；TTS 音频，非真人双讲） |
| 进程级 loopback（按进程采集） | 能力探测已实现；目标应用 **NOT_VERIFIED** |
| ASR 评测数据集（合成 TTS） | **已建立**（P3.5；11 个样本，真实会议录音不入库） |
| CER/WER 工具 | **已实现**（P3.5；含归一化、micro/macro、静音幻觉） |
| Provider 对比（合成 TTS） | **PARTIAL**：Aliyun PASS（CER 12.54%）；Deepgram **NOT_RUN**（无 Key） |
| Segment 延迟（合成 TTS） | **已测**：Aliyun finalization P50 771.9ms / P95 2,228.2ms；非会议实时延迟 |
| 真实会议 WER | **NOT_MEASURED** |
| 腾讯会议 / 微信 PC 兼容 | **NOT_VERIFIED** |
| DeepSeek 问答、悬浮窗 | 尚未实现 |

性能目标（P50 < 1s / P95 < 2s）在后续阶段测量，**当前没有任何延迟数据**，详见 [TEST_PLAN.md](TEST_PLAN.md)。

## 设计文档

| 文档 | 内容 |
|---|---|
| [ARCHITECTURE_REVIEW.md](ARCHITECTURE_REVIEW.md) | 对 DeepSeek Harness 仓库的事实审查、可复用模块、风险与未知问题 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 目标架构：双模式、三进程、数据流、安全与隐私模型 |
| [PLUGIN_API.md](PLUGIN_API.md) | Harness 插件契约、事件协议 v1、Provider 接口、运行时配置 |
| [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) | 十阶段的文件、任务、依赖、验收标准 |
| [TEST_PLAN.md](TEST_PLAN.md) | 测试分层、延迟测量协议、安全测试、兼容矩阵 |
| [BUILD_LOG.md](BUILD_LOG.md) | 实际执行记录与真实测试结果 |
| [SECURITY.md](SECURITY.md) | 信任边界、令牌、文件加固、日志脱敏、不做输入注入 |

## 仓库结构

```text
packages/protocol/       # 协议事实源：事件 JSON Schema、音频帧协议、生成的 TS/Python 模型、跨语言 fixtures
services/copilot-core/   # Python FastAPI：事件通道、音频帧接收与统计、SQLite、配置、日志
services/audio-capture/  # C# + NAudio：WASAPI loopback/麦克风采集、二进制音频发送、控制模式与模拟源
apps/desktop/            # Electron 主进程 + Vue3 渲染进程（状态展示）
scripts/e2e-smoke.ps1    # 真实 Core + 真实 C# 客户端的握手冒烟
scripts/e2e-audio.ps1    # 真实 Core + 真实 C# 客户端 60 秒音频帧传输与丢帧检测
scripts/capture-smoke.ps1 # 真实声卡 loopback 采集 + 电平校验（PASS/SKIP/FAIL，需音频设备）
scripts/e2e-control.ps1 # 真实 Core + 真实 sidecar 的控制面端到端
scripts/control_client.py # ui 角色驱动 capture.start/stop 的测试客户端
scripts/capture-stability.ps1 # 30 分钟稳定性采样与报告
tools/capture-probe.ps1 # 机器可读诊断报告（系统/设备/参数/统计）
tools/tone-player/       # 诊断工具：通过 WASAPI 播放测试音，仅供 capture-smoke 使用
tests/fixtures/          # 测试数据（Phase 2-B 起加入音频样本）
```

依赖方向：`apps/desktop` → `packages/protocol`；`services/copilot-core` → `packages/protocol`；`services/audio-capture` 只依赖线上协议。不存在反向依赖。

## 环境要求

| 工具 | 版本（本项目验证环境） |
|---|---|
| Windows | 10 22H2 / 11（音频采集在 Phase 2） |
| Node.js | 24.22.1（`>=22.19`） |
| pnpm | 11.28.5 |
| Python | 3.13.15（`>=3.11`） |
| uv | 0.13.9 |
| .NET SDK | 10.0.404（LTS，目标框架 `net10.0`） |

## 快速开始

```powershell
# 1) 依赖
uv sync                 # Python 工作区（core + protocol）
pnpm install            # protocol + desktop

# 2) 启动 Core（自动分配回环端口，写入 %LOCALAPPDATA%\RealTimeCopilot\runtime.json）
uv run python -m copilot_core

# 3) 另开一个终端：桌面端
pnpm -C apps/desktop build
pnpm -C apps/desktop start

# 4) 音频进程：控制模式由 Core 拉起；也可独立运行发送 60 秒模拟音频
dotnet build services/audio-capture/AudioCapture.csproj -c Release
# 5a) 模拟音频（无需声卡，CI 使用）
dotnet services/audio-capture/bin/Release/net10.0/audio-capture.dll --simulate-audio --seconds=60 --drop-every=500

# 5b) 真实采集：系统输出 + 可选麦克风
dotnet services/audio-capture/bin/Release/net10.0/audio-capture.dll --capture-loopback --seconds=60
dotnet services/audio-capture/bin/Release/net10.0/audio-capture.dll --capture-loopback --capture-mic --seconds=60

# 5c) 列出音频端点
dotnet services/audio-capture/bin/Release/net10.0/audio-capture.dll --list-devices
```

Core 的配置来自 `%LOCALAPPDATA%\RealTimeCopilot\config.toml`，可用 `RTC_*` 环境变量覆盖（例如 `RTC_SERVER_PORT`、`RTC_LOG_LEVEL`、`RTC_DATA_DIR`）。非法配置会让进程以退出码 2 结束。

## 测试

| 范围 | 命令 | 当前结果 |
|---|---|---|
| Python（协议 + Core + 音频 + 控制面 + ASR + 问题检测） | `uv run pytest -q` | 372 passed, 3 skipped |
| Python 静态检查 | `uv run ruff check .` | All checks passed |
| C# | `dotnet test services/audio-capture/tests -c Release` | 91 passed |
| 协议（TypeScript） | `pnpm -C packages/protocol test` | 28 passed |
| 桌面（Vue/主进程逻辑） | `pnpm -C apps/desktop test` | 11 passed |
| 类型检查 | `pnpm -r --if-present run typecheck` | 通过（含 vue-tsc） |
| 握手端到端 | `pwsh -File scripts/e2e-smoke.ps1` | PASS |
| 音频端到端（60s） | `pwsh -File scripts/e2e-audio.ps1 -Seconds 60` | PASS：2995 帧、注入 6 次丢帧全部识别 |
| 真实采集冒烟 | `pwsh -File scripts/capture-smoke.ps1` | PASS（三态判定）：峰值 -4.4 dBFS |
| 控制面端到端 | `pwsh -File scripts/e2e-control.ps1` | PASS：300 帧、idle、无进程残留 |
| 30 分钟稳定性 | `pwsh -File scripts/capture-stability.ps1 -Minutes 30` | 报告见 `.artifacts/stability` |

详细结果与命令输出见 [BUILD_LOG.md](BUILD_LOG.md)。

## 协议约定

- JSON Schema 是事件协议的唯一事实源：`packages/protocol/schema/events.v1.json`。
- 音频二进制协议：`packages/protocol/audio-frame.v1.md`（28 字节头 + 640 字节 PCM16LE，668 字节/帧）。
- 修改事件协议后必须运行 `pnpm -C packages/protocol generate` 并提交生成结果；CI 会用 `git diff --exit-code` 校验两者同步。
- 修改音频帧格式必须同步：Python `audio/frame.py`、C# `AudioFrameCodec`、fixtures 生成脚本与协议文档。
- 所有事件共用信封：`v` / `type` / `eventId` / `requestId?` / `ts` / `payload`。
- 未知事件被忽略（前向兼容），版本不兼容与结构错误会回 `system.error` 并关闭连接。

## 安全

录音必须由用户主动开启；本地服务只监听 `127.0.0.1`；令牌随机生成且不进日志；**没有任何向会议软件注入文字或模拟输入的代码路径**。详见 [SECURITY.md](SECURITY.md)。

## 阶段进度

| 阶段 | 状态 |
|---|---|
| P0 架构审查与设计 | 完成 |
| P1 基础工程（协议 / Core / 音频骨架 / 桌面骨架 / CI） | 完成 |
| P2-A 音频二进制协议与 Core 接收通道 | 完成 |
| P2-B Windows 音频采集（WASAPI） | 完成（设备级 loopback + 可选麦克风） |
| P2-C 采集控制面、票据、诊断 | 完成 |
| P2-C.1 控制面并发与清理加固 | 完成 |
| P2-C.2 最终加固（锁外 spawn、任务绑定、票据边界） | 完成 |
| P2-D / P2-D.1 音频可靠性 | 按来源统计、Channel、稳定性、设备恢复策略与 loopback 能力探测完成；实机热插拔与目标应用验证 NOT_VERIFIED |
| P3 / P3.2.2 流式 ASR | VAD、会话级 Provider 运行时、Deepgram 参数与自检脚本完成；真实识别 **NOT_VERIFIED**（无 Key） |
| P4 问题检测 | 未开始 |
| P5 DeepSeek 问答 | 未开始 |
| P6 Electron 悬浮窗 | 未开始 |
| P7 Harness 集成 | 未开始 |
| P8 独立运行 | 未开始 |
| P9 性能优化 | 未开始 |
| P10 Windows 打包 | 未开始 |
