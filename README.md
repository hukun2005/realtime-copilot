# RealTime Copilot

Windows 实时 AI 会议助手：采集电脑输出音频（可选麦克风）→ 流式语音识别 → 识别问题 → DeepSeek 流式生成口语化答案 → 桌面悬浮窗展示。支持 **独立运行** 与 **DeepSeek Harness 集成** 两种模式。

## 当前状态

**Phase 1（基础工程）已完成并验证。** 当前是可运行、可编译、可测试的跨语言骨架：

- 统一协议（JSON Schema → TypeScript / Pydantic / 手写 C# DTO）
- Python Core：WebSocket 鉴权、心跳、错误模型、TOML 配置、SQLite 迁移、JSON 日志
- C# 音频进程骨架：连接 Core、握手、心跳应答、断线识别、优雅退出
- Electron + Vue3 桌面骨架：状态展示、IPC 白名单、Core 连接
- GitHub Actions 四作业 CI（含 Windows 真实端到端）

**尚未实现（Phase 2 起）**：WASAPI 采集、VAD、ASR、问题检测、DeepSeek 问答、悬浮窗。界面会明确显示"尚未采集音频"。

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
packages/protocol/       # 协议事实源：schema、生成的 TS/Python 模型、跨语言 fixtures
services/copilot-core/   # Python FastAPI：/healthz、/ws/v1/events、SQLite、配置、日志
services/audio-capture/  # C# + NAudio：连接 Core 的音频进程骨架（无采集）
apps/desktop/            # Electron 主进程 + Vue3 渲染进程（状态展示）
scripts/e2e-smoke.ps1    # 真实 Core + 真实 C# 客户端端到端冒烟
tests/fixtures/          # 测试数据（Phase 2 起加入音频样本）
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

# 4) 或：音频进程骨架
dotnet build services/audio-capture/AudioCapture.csproj -c Release
dotnet services/audio-capture/bin/Release/net10.0/audio-capture.dll
```

Core 的配置来自 `%LOCALAPPDATA%\RealTimeCopilot\config.toml`，可用 `RTC_*` 环境变量覆盖（例如 `RTC_SERVER_PORT`、`RTC_LOG_LEVEL`、`RTC_DATA_DIR`）。非法配置会让进程以退出码 2 结束。

## 测试

| 范围 | 命令 | 当前结果 |
|---|---|---|
| Python（协议 + Core） | `uv run pytest -q` | 77 passed, 1 skipped |
| Python 静态检查 | `uv run ruff check .` | All checks passed |
| C# | `dotnet test services/audio-capture/tests -c Release` | 27 passed |
| 协议（TypeScript） | `pnpm -C packages/protocol test` | 22 passed |
| 桌面（Vue/主进程逻辑） | `pnpm -C apps/desktop test` | 11 passed |
| 类型检查 | `pnpm -r --if-present run typecheck` | 通过（含 vue-tsc） |
| 端到端（真实进程） | `pwsh -File scripts/e2e-smoke.ps1` | PASS |

详细结果与命令输出见 [BUILD_LOG.md](BUILD_LOG.md)。

## 协议约定

- JSON Schema 是唯一事实源：`packages/protocol/schema/events.v1.json`。
- 修改协议后必须运行 `pnpm -C packages/protocol generate` 并提交生成结果；CI 会用 `git diff --exit-code` 校验两者同步。
- 所有事件共用信封：`v` / `type` / `eventId` / `requestId?` / `ts` / `payload`。
- 未知事件被忽略（前向兼容），版本不兼容与结构错误会回 `system.error` 并关闭连接。

## 安全

录音必须由用户主动开启；本地服务只监听 `127.0.0.1`；令牌随机生成且不进日志；**没有任何向会议软件注入文字或模拟输入的代码路径**。详见 [SECURITY.md](SECURITY.md)。

## 阶段进度

| 阶段 | 状态 |
|---|---|
| P0 架构审查与设计 | 完成 |
| P1 基础工程（协议 / Core / 音频骨架 / 桌面骨架 / CI） | 完成 |
| P2 Windows 音频采集 | 未开始 |
| P3 流式 ASR | 未开始 |
| P4 问题检测 | 未开始 |
| P5 DeepSeek 问答 | 未开始 |
| P6 Electron 悬浮窗 | 未开始 |
| P7 Harness 集成 | 未开始 |
| P8 独立运行 | 未开始 |
| P9 性能优化 | 未开始 |
| P10 Windows 打包 | 未开始 |
