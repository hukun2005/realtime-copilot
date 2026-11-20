# BUILD LOG

按时间顺序记录本项目的实际动作、证据与决策。**只记录真实发生的事**；计划与设想写在 `IMPLEMENTATION_PLAN.md`，两者不混。

---

## 2026-11-20 · Phase 0：仓库审查与设计文档

### 输入

- 工作目录：DSH 的默认工作区（本机路径已隐去；非产品仓库）
- 被审查仓库：DeepSeek Harness 源码检出（本机路径已隐去）
  - branch `master`，HEAD `687f282e86`（Merge PR #6408, release `0.2.1-rc.2`）
  - `git status --porcelain` 仅一行 `?? default-workspace/`（既有未跟踪目录）
- 约束：本阶段只审查、设计、写文档；不修改业务代码；不删除或覆盖现有功能。

### 实际执行的命令（可复现）

| 命令 | 目的 | 结果 |
|---|---|---|
| `Get-Location` / `Get-ChildItem` | 确认工作区与安装目录 | 工作区含若干既有项目目录；安装目录里的 `app.asar\dsh` 不是目录（asar 打包），源码以独立检出为准 |
| `git -C <repo> status --porcelain` | 确认干净度 | 仅 `?? default-workspace/` |
| `git -C <repo> rev-parse --abbrev-ref HEAD` / `log -1 --oneline` | 版本定位 | `master` / `687f282e86` |
| `Get-Content package.json` | 工具链与脚本 | Node `^22.19 \|\| >=24`、pnpm 11.28.5、342 个 workspace |
| `Get-ChildItem packages` / `apps` | 目录结构 | 52 个包组、5 个 app、342 个带 `package.json` 的包 |
| `read docs/architecture.md` | 权威架构 | profiles/bundles、核心包表、事件分类、扩展点表 |
| `read docs/testing.md` | 测试分层 | unit/coverage/e2e/expected/snapshot/web/bench |
| `read docs/cookbook/adding-a-package.md` | 包清单不变量 | peer/dev 依赖、exports、files 规则 |
| `read docs/subsystems/llm-streaming.md`（860 行） | 模型接缝 | `StreamChunk`、adapter 契约、`GenerateOptions`、`PreparedLlmCall` |
| `read packages/bundle/base/cordis.patch.yml` | 组合格式 | 一行一条 `insert`，按 `id` 定位 |
| `read packages/context/time-context/src/index.ts` | 插件模板 | `name`/`inject`/`Config`/`apply`，`agent/pre-step` 注入 |
| `read packages/interaction/permission-presets/README.md` | 权限模型 | sandbox + approval 打包成 preset |
| `read packages/sandbox/sandbox-policy/README.md` | 沙箱语义 | 三档模式，默认 `read-only` |
| `read packages/llm/llm-deepseek-api-key/README.md` | 凭证 | `DEEPSEEK_API_KEY`、credentials 服务优先 |
| `grep assertOwnerOnly/mode` in `credentials-local/src/index.ts` | 密钥存储 | 文件 `0600/0700`，Windows 无 POSIX mode 校验 |
| `read packages/sdk/server/README.md` | 进程外集成 | stdio JSON-RPC，无 per-prompt 结果 |
| `read packages/api/gateway/README.md` | Web RPC 性质 | Typert + Peer + remoteAccess，内部接口 |
| `read packages/mcp/mcp-resources/README.md` | 知识入口 | MCP 资源三工具，按需读取 |
| `read packages/client/AGENTS.md`（前 140 行）+ `ui-goal/package.json` | 前端栈 | React 18 + Slot 体系，`dsh.client.inject` |
| `read apps/desktop/package.json` + README 关键段 | 桌面与打包 | Electron 44、electron-builder、win-x64 全链路、麦克风权限 |
| `read python/sdk/pyproject.toml` | Python 基线 | `requires-python >=3.10`、pydantic v2、uv |
| `grep knowledge` in `packages/**/*.ts` | 知识库接口 | 只命中 `acknowledge` 类词，无知识库服务 |
| `web_search`（进程 loopback 最低版本 / DeepSeek 流式延迟） | 外部事实核对 | 微软文档：进程级 loopback 最低 Win10 build 20348；实践社区称 19041 起可用，无官方承诺 |

**未执行**：`pnpm install`、`pnpm run build`、`pnpm test`、启动 `dsh`。本阶段不需要，且避免在他人仓库留下构建产物。

### 关键发现（写入文档的结论）

1. Harness 是 Cordis 全插件 Node/TS 应用；集成单位是 profile/bundle/plugin，其次是 SDK JSON-RPC 与 ACP。没有对外 HTTP 业务 API。
2. 前端是 React 18，不存在 Vue3 → Vue3 只能用于我们自己的 Electron 窗口。
3. 没有知识库接口 → 自建，并可选以 MCP server 形式暴露。
4. 模型接缝是 `ctx.llm`，DeepSeek 适配器已存在；但第三方直接调用属于未宣传用法，需实测（U6）。
5. 权限是 sandbox mode + approval policy 两个旋钮，默认 `read-only`。
6. 凭证在 Windows 上只靠 ACL，无 POSIX mode 校验 → 独立模式改用 `safeStorage`。
7. 音频/ASR/VAD 在 Harness 中完全不存在，全新工程。
8. 桌面端已有完整的 Windows 打包、签名、托盘、麦克风权限实现，可作为**模式参考**。

### 决策（本阶段）

| ID | 决策 | 依据 |
|---|---|---|
| D1 | Core 用 Python，UI/桥接用 TS | ASR 生态；Harness 插件必须是 Node 包；进程隔离 |
| D2 | 三进程：UI / Core / Audio | 故障隔离（驱动崩溃不带走 UI） |
| D3 | C# + NAudio 做采集 | WASAPI 进程级 loopback 实现最成熟 |
| D4 | WS + JSON 事件，音频走二进制帧 | 跨语言成本最低；本机开销可忽略 |
| D5 | Vue3 仅用于自有窗口 | Harness 客户端是 React |
| D6 | 知识库 SQLite FTS5 自建 | Harness 无 KB 接缝；零外部依赖 |
| D7 | 延迟锚点 = VAD 段尾时间戳 | 用 ASR 返回时刻会系统性隐藏延迟 |
| D8 | v1 不做输入注入 | 安全要求；无代码路径即无风险 |

### 产出文件

| 文件 | 说明 |
|---|---|
| `README.md` | 项目入口与当前状态（明确"未实现、未测量"） |
| `ARCHITECTURE_REVIEW.md` | 事实审查、可复用/新增、风险登记、未知问题 |
| `ARCHITECTURE.md` | 目标架构、双模式、拓扑、预算、数据模型、安全模型、ADR |
| `PLUGIN_API.md` | Harness 插件契约、事件协议 v1、Provider 接口、运行时配置 |
| `IMPLEMENTATION_PLAN.md` | 十阶段：文件、任务、依赖、验收、测试命令 |
| `TEST_PLAN.md` | 分层、fixture、延迟测量协议、安全测试、兼容矩阵、门禁 |
| `BUILD_LOG.md` | 本文件 |

### 明确未完成 / 未验证

- **没有任何代码**，因此**没有任何延迟数据**。P50 < 1s / P95 < 2s 是目标，不是结果。
- 未验证：
  - U1 DeepSeek 首 token 分布
  - U2 ASR 选型与 WER
  - U3 Win10 19044/19045 上进程级 loopback 的实际可用性
  - U4 腾讯会议/微信是否走受保护音频路径
  - U6 第三方插件调用 `ctx.llm.stream()` 的可行性
  - U7 `setContentProtection` 在各共享方式下的效果
  - U9 `dsh plugin` 安装树外 bundle 的依赖解析
- 未在 Windows 上构建 Harness 本体（也不需要；我们不修改它）。

### 下一步

Phase 1：建立 `packages/protocol` 的事件 schema 与双端生成、`services/copilot-core` 的 WS 鉴权骨架、`services/audio-capture` 的空客户端、`apps/desktop` 的空窗口，跑通三语言 CI，并记录冷启动与内存基线。

## 2026-11-20 · Phase 1：基础工程

### 环境（本机实测）

Node 24.22.1 · pnpm 11.28.5 · Python 3.13.15 · .NET SDK 10.0.404（运行时 10.0.15）· uv 0.13.9（本机原先未安装，通过 `python -m pip install uv` 补装）。
`pnpm-workspace` 管理 TS 包；uv workspace 管理 Python；`dotnet` 管理 C#。目标框架选 `net10.0`（本机唯一 SDK，且为 LTS）。

### 实际执行与结果

| 命令 | 结果 |
|---|---|
| `pnpm -C packages/protocol generate` | 生成 `types.ts` 与 `models.py`，无错误 |
| `pnpm -C packages/protocol test` | 22 passed |
| `pnpm -C packages/protocol typecheck` | 通过（`tsc` build + test 配置） |
| `uv sync` | 解析 29 个包，安装 workspace 成员 `copilot-protocol` / `copilot-core` |
| `uv run ruff check .` | All checks passed |
| `uv run pytest -q` | 77 passed, 1 skipped（Windows 跳过 POSIX `0600` 断言） |
| `dotnet build services/audio-capture -c Release` | 0 warning / 0 error |
| `dotnet test services/audio-capture/tests -c Release` | 27 passed |
| `pnpm -C apps/desktop typecheck` | 通过（tsc + vue-tsc） |
| `pnpm -C apps/desktop build` | renderer 67.34 kB（gzip 26.58 kB），main 由 tsc 输出 |
| `pnpm -C apps/desktop test` | 11 passed |
| `pwsh -File scripts/e2e-smoke.ps1 -Seconds 2` | PASS：Core 健康、客户端 `core ready`、`roles={"audio":1}` |
| Electron smoke（`electron . --smoke`） | exit 0，输出 `{"smoke":"connected","rendered":true,...}`；断言 Vue 已挂载并渲染出"Core 服务 / 已连接" |

补充验证：

- `Get-NetTCPConnection -LocalPort <port> -State Listen` → `LocalAddress=127.0.0.1`，确认只监听回环。
- `runtime.json` 写入日志出现 `hardened:true`，说明 Windows 上 `icacls` 加固成功。
- 强杀客户端后 Core 日志无 `Exception in ASGI` / `Traceback`（初次实现有，已修复）。

### 交付物

| 路径 | 内容 |
|---|---|
| `packages/protocol/` | schema、生成器、TS 类型、Pydantic 模型、运行时编解码、8+5 个 fixtures、22 个测试 |
| `services/copilot-core/` | FastAPI、WS 鉴权与心跳、配置、日志、SQLite 迁移、安全模块、50 个测试 |
| `services/audio-capture/` | .NET 10 客户端骨架、协议 DTO、配置、Kestrel 驱动的 27 个测试 |
| `apps/desktop/` | Electron 主进程、preload、Vue3 渲染进程、状态机、11 个测试 |
| `scripts/e2e-smoke.ps1` | 真实 Core + 真实 C# 客户端的端到端冒烟 |
| `.github/workflows/ci.yml` | 4 个作业：python / dotnet / node / windows-e2e |
| `SECURITY.md` | 信任边界、令牌、ACL 与 DPAPI 取舍、残余风险 |

### 设计决策与偏离

1. **新增错误码 `not_implemented`**：`PLUGIN_API.md` 的 `ErrorCode` 原本只有 8 个值。Phase 1 的 `capture.start` / `capture.stop` / `audio.status` 已定义但未实现，用 `unsupported_event` 会误导（那是"未知事件"）。因此 schema 增加该枚举值，并同步生成双端模型。
2. **协议生成方式**：TS 用 `json-schema-to-typescript`，Python 用 `datamodel-code-generator`（`uvx` 固定 0.99.0）。C# DTO 手写，用共享 fixtures 做往返约束——没有可靠的 C# JSON Schema 代码生成器能在无额外依赖下保证一致。
3. **uvicorn WebSocket 实现选 `websockets-sansio`**：`websockets` 实现在 uvicorn 0.54 已弃用；切换后不再出现 `websockets.legacy` 与 `--ws websockets` 的弃用警告。
4. **`runtime.json` 不加密**：DPAPI 密文与 Electron `safeStorage` 格式不互通，加密会切断三进程共享。改为"用户级目录 + `icacls` + 每次启动轮换令牌 + 回环绑定"，理由写在 `SECURITY.md` §4。
5. **桌面端不启动 Core**：Phase 1 只"连接"，进程编排属于 Phase 8。桌面端从 `runtime.json` 读地址与令牌，读不到就显示明确错误。
6. **`runtime.json` 端口可信**：Core 先 `socket.create_server` 拿到真实端口，再交给 uvicorn，避免"写 0 端口再猜"。

### 遇到的问题与修复

| 问题 | 原因 | 修复 |
|---|---|---|
| `HelloPayload` 无 `clientName` 属性 | 生成模型用 snake_case + alias，代码里读了 camelCase | 改读 `client_name` / `client_version`；C# 侧用 `JsonPropertyName` 保持线格式 |
| `runtime.json` 缺 `wsUrl` | `asdict()` 输出 `ws_url` | `RuntimeInfo.to_json()` 显式映射 camelCase |
| 强杀客户端时 Core 打 ASGI traceback | 对端消失后 `send`/`close` 抛 `ClientDisconnected`，只捕获了 `WebSocketDisconnect` | 发送与关闭改为 best-effort（捕获后按已关闭处理），接收循环外层增加兜底日志 |
| `role` 日志输出 `<Role:role>` | 记录的是 Pydantic RootModel | 登录时转成裸字符串 |
| 打包后 `copilot_core` 导入失败 | hatchling `packages` 指向了 `copilot_core` 而非 `src/copilot_core` | 修正打包路径 |
| Electron 启动报 `Cannot read properties of undefined (reading 'whenReady')` | 本机环境继承了 `ELECTRON_RUN_AS_NODE=1`，Electron 以 Node 模式运行 | 冒烟前清除该变量；`apps/desktop/README.md` 已注明 |
| `dotnet test` 找不到 `Fact` | xunit 没有全局 using | 增加 `GlobalUsings.cs` |
| Electron 提示 Insecure-CSP；加 CSP 后页面白屏 | 原本没有 CSP；而 `file://` 下 `script-src 'self'` 匹配不到打包后的模块脚本 | 改为自定义协议 `copilot://app/`（`protocol.handle` + 越界 403），CSP 使用 `'self'`；警告消失且页面正常渲染 |
| 隐藏窗口读 `innerText` 为空 | `innerText` 依赖布局，隐藏窗口不保证布局 | smoke 断言改用 `textContent` |

### 尚未解决 / 下一阶段前置

- 音频采集完全未实现（Phase 2）：WASAPI 设备级与进程级 loopback、重采样、分帧、二进制帧通道。
- Core 尚未处理 `role=audio` 的音频帧（当前回 `not_implemented`）。
- 桌面端还没有悬浮窗形态、托盘与设置页（Phase 6）。
- `safeStorage` 与 API Key 流程未接入（Phase 5）。
- CI 首次运行结果需在本轮推送后确认；Windows 作业依赖 GitHub 托管 runner 的 .NET 10 支持。

### 下一步

Phase 2：实现 `IAudioFrameSource` 的 WASAPI 设备级采集（先不做进程级），把 20ms PCM16 帧通过二进制 WebSocket 帧发给 Core，并在 Core 侧统计帧率与丢帧；随后按 U3/U4 在两个 Windows 版本上实测腾讯会议与微信。

## 2026-11-20 · Phase 2-A：音频二进制协议与 Core 接收通道

分支：`feature/phase-2a-audio-protocol`（基线 `cb30f39`；基线测试 77 Python / 27 C# / 22 协议 / 11 桌面 全绿）。

### 协议

28 字节小端头 + 640 字节 PCM16LE = 668 字节/帧，一条 WebSocket Binary Message 一帧。
字段：`protocolVersion`、`source`、`frameDurationMs`、`sequence`、`captureTsNs`、`sampleRate`、`channels`、`sampleFormat`、`payloadLength`。
完整定义见 `packages/protocol/audio-frame.v1.md`；跨语言 fixtures 由 `packages/protocol/scripts/make-audio-fixtures.py` 确定性生成（7 个 `.bin` + 元数据）。

### 实际执行与结果

| 命令 | 结果 |
|---|---|
| `uv run ruff check .` | All checks passed |
| `uv run pytest -q` | **113 passed, 1 skipped**（新增 36 个音频测试） |
| `dotnet build services/audio-capture -c Release` | 0 warning / 0 error |
| `dotnet test services/audio-capture/tests -c Release` | **55 passed**（新增 28 个） |
| `pnpm -C packages/protocol test` | 22 passed |
| `pnpm -C apps/desktop test` | 11 passed |
| `pnpm -r --if-present run typecheck` | 通过 |
| `pwsh -File scripts/e2e-smoke.ps1` | PASS（握手链路未回归） |
| `pwsh -File scripts/e2e-audio.ps1 -Seconds 10 -DropEvery 50` | PASS：sent=491，core frames=491，gaps=10 |
| `pwsh -File scripts/e2e-audio.ps1 -Seconds 60 -DropEvery 500` | **PASS：sent=2995，skipped=6，core frames=2995，bytes=1,916,800，gaps=6，invalid=0，duplicates=0，rateLimited=0，sessions=0** |

C# 序列化结果与 `valid-frame.bin` **逐字节相同**（`AudioFrameCodecTests.Serialize_MatchesCrossLanguageFixture`），说明两端对布局与 ramp 负载的理解一致。

### 设计决策

1. **不加 magic 字段**：WebSocket 已提供消息边界；严格的长度/版本/采样率/负载校验足以拒绝随机字节，头部保持 28 字节。
2. **序号按 source 分轨**：同一连接可同时发 loopback 与 mic，各自从任意起点递增，`uint32` 回绕；判定用 `(seq - last) mod 2^32`，一半窗口内视为丢帧，否则视为重复/乱序。
3. **帧级错误不断连**：实时音频丢一帧优于断流；1 秒窗口内无效帧 > 25 或超速 > 10 次才断开。
4. **统计放 `/healthz` 而不是新增事件类型**：避免在本阶段扩展事件枚举；桌面端已有轮询路径，后续需要实时推送时再加 `audio.stats` 事件。
5. **新增 `rate_limited` 错误码**：语义比复用 `invalid_payload` 准确，schema 已重新生成。
6. **短期音频 ticket 只设计不实现**：需要 `capture.start` 的请求-响应通道（Phase 2-B），强行实现会破坏 `runtime.json` 启动方式；`SECURITY.md §7` 记录了设计与未实现原因。
7. **模拟源用确定性 ramp**：与 fixtures 同算法，跨平台字节一致，便于跨语言断言；真实 WASAPI 在 2-B 替换。

### 遇到的问题与修复

| 问题 | 原因 | 修复 |
|---|---|---|
| 日志泄露测试整包运行失败、单独跑通过 | `caplog` 同时抓到 `websockets`/`httpcore` 的调试日志（含 JSON 的 `payload` 字段） | 断言只针对 `copilot_core` 命名空间的记录，并先断言"确实抓到了应用日志" |
| C# 测试编译失败：`AudioFrame` 无 `sequence` 参数 | 记录参数是 PascalCase | 改为 `Sequence:` / `CaptureTimestampNs:` |
| C# 测试另一处编译失败：`out _` 类型冲突 | lambda 参数命名为 `_`（CancellationToken），`out _` 解析到它 | 参数改名 `token`，改用 `out var parseError` |
| 裸开关 `--simulate-audio` 被配置解析拒绝 | `AppConfig.ParseArguments` 只接受 `--key=value` | 裸开关按 `"true"` 记录，由消费方解释 |
| 二进制帧从 `ui` 角色发送时旧测试期望 `not_implemented` | 行为已按安全要求改为拒绝并断开 | 更新为 `invalid_role` + 连接关闭（断言更强，不是放宽） |

### 未完成 / 风险

- 真实 WASAPI 采集未实现（Phase 2-B）；`capture.start` / `capture.stop` 仍返回 `not_implemented`。
- 角色是自报的，不等同进程身份；短期 ticket 仅有设计。
- 进程级 loopback 的 Windows 版本门槛（U3）与受保护音频路径（U4）仍需实机验证。
- 本轮为 Windows 本地验证；CI（含 60 秒音频作业）在推送后确认。

### 下一步

Phase 2-B：用 `WasapiLoopbackCapture` 替换 `SimulatedAudioSource`，接入重采样与 20ms 分帧，保持 `IAudioFrameSource` 与 `AudioStreamer` 的接口不变；随后在 Win10 22H2 与 Win11 上实测腾讯会议/微信。

## 2026-11-20 · Phase 2-B：WASAPI 设备级采集

分支：`feature/phase-2b-wasapi-capture`（基线 `04b8338`）。

### 实现

- `Capture/WasapiCaptureSource.cs`：`WasapiRecorderBuilder`（NAudio 3.3 起 `WasapiCapture`/`WasapiLoopbackCapture` 已标记过时）→ `BufferedWaveProvider` → `StereoToMonoSampleProvider` → `WdlResamplingSampleProvider(16000)`；`WAVE_FORMAT_EXTENSIBLE` 先 `AsStandardWaveFormat()` 归一化，否则 `ToSampleProvider` 无法识别 float。
- `Audio/AudioFramer.cs`：跨回调字节累积、20ms 切帧、时间戳按已发帧数推进。
- `Audio/Pcm16.cs`：float → PCM16LE，限幅到 `short.MinValue..MaxValue`。
- `Audio/FrameStreamer.cs`：采集事件 → 有界 channel（200 帧，满了丢最旧）→ 顺序发送；采集失败与发送失败分别抛出 `CaptureFailedException` / `CoreConnectionException`。
- Core：新增 `audio/level.py`（峰值/RMS dBFS），`/healthz` 增加 `peakDbfs`（整段最大）与 `lastPeakDbfs`（最后一帧）。
- 工具：`tools/tone-player`（诊断用，不随产品发布）；`scripts/capture-smoke.ps1` 串起"播放测试音 → 真实采集 → 断言帧数与电平"。

### 实际执行与结果

| 命令 | 结果 |
|---|---|
| `dotnet build services/audio-capture -c Release` | 0 warning / 0 error |
| `dotnet test services/audio-capture/tests -c Release` | **74 passed**（新增 19：分帧、PCM16、FrameStreamer、采集参数） |
| `uv run pytest -q` | **123 passed, 1 skipped**（新增 10：电平、会话峰值） |
| `pnpm -C packages/protocol test` | 22 passed |
| `pnpm -C apps/desktop test` | 11 passed |
| `pwsh -File scripts/e2e-audio.ps1 -Seconds 10 -DropEvery 50` | PASS：491 帧、10 次注入丢帧全部识别 |
| `pwsh -File scripts/capture-smoke.ps1 -Seconds 8` | **PASS：398 帧、0 丢帧、0 无效帧、整段峰值 -8.7 dBFS**（测试音理论峰值 -8.7 dBFS） |
| 显式端点：`--capture-device={...}` + 4 秒测试音 | 297 帧、峰值 -10.5 dBFS |
| `dotnet ... --list-devices` | 1 个渲染端点 + 1 个采集端点 |

### 遇到的问题与修复

| 问题 | 原因 | 修复 |
|---|---|---|
| 采集一次性发出 365 帧，Core 回 `rate_limited` | 泵任务无节拍，把 2 秒缓冲瞬间拉空 | 按"已发帧数 × 20ms"限速；落后 2 秒清空积压 |
| 真实采集全为静音（跨进程播放） | `MMDeviceEnumerator` 被 `using` 提前 Dispose，返回的 `MMDevice` 失效；此时 `WithDevice(...)` 打开的是无效端点 | 枚举器改为进程级 `Lazy`，不提前释放；默认端点交给 builder 解析 |
| 用 `WithDevice(显式默认端点)` 采集为静音 | 同上，端点对象生命周期 | 同上；显式端点修复后峰值 -10.5 dBFS |
| 采集帧数据被补零 | `BufferedWaveProvider.ReadFully` 默认为 true，缓冲不足时补零 | 设 `ReadFully = false`，不足时返回已有样本 |
| 冒烟脚本报"静音"但实际有声音 | 只比较了**最后一帧**电平，而播放结束后末帧本来就是静音 | Core 增加整段 `peakDbfs`，脚本改用它 |
| 冒烟脚本无声音 | `SoundPlayer` 在 pwsh 7 不可用（`System.Windows.Extensions`），`powershell.exe` 在远程会话下也未出声 | 改用 `tools/tone-player` 通过 WASAPI 播放 |
| 全套 Python 测试在机器负载高时偶发失败 | 测试配置把心跳超时压到 1.2s、鉴权 400ms，事件循环被抢占即误判断线 | 测试配置改为鉴权 2s、心跳 500ms/超时 10s（产品默认仍是 30s） |
| `CA1416` 平台告警 | 静态字段直接构造 `MMDeviceEnumerator` | 字段标注 `[SupportedOSPlatform("windows")]` |

### 未完成 / 风险

- 进程级 loopback（`WithProcessLoopback`）未实现 → Phase 2-C。
- 30 分钟稳定性、CPU/内存预算、腾讯会议/微信/浏览器实机、设备热插拔、静音事件、supervisor 拉起均未验证。
- 麦克风采集代码路径已接，未用真实麦克风验证。
- CI 不跑真实采集（托管 runner 无音频端点）；CI 仍跑模拟音频 60 秒端到端。

### 下一步

Phase 2-C：进程级 loopback（按进程采集）+ 设备热插拔 + 静音诊断；随后 Phase 3 接 ASR。

## 2026-11-20 · Phase 2-C：采集控制面、票据与诊断

分支：`feature/phase-2c-capture-control`（基线 `5a8163d`）。

### 实现

- `capture/state.py`：五状态机（idle/starting/capturing/stopping/error）+ 合法迁移表 + `InvalidTransition`。
- `capture/controller.py`：Core 侧控制面。spawn sidecar（`RTC_CAPTURE_CONTROL=1` + 一次性票据）、
  启动/停止超时、幂等 start/stop、sidecar 退出与断连进 error、状态广播给 UI。
- `tickets.py`：HMAC 票据（session + nonce + expiry），一次性消费，stop/崩溃/关闭即撤销。
- 协议：`HelloPayload.ticket`、`AudioStatusPayload.mode/errorCode`、新增错误码
  `invalid_state/capture_timeout/sidecar_unavailable/ticket_invalid`。
- C# `Control/CaptureControl.cs`：控制模式执行器，动态启停采集、上报 audio.status、默认设备变化检测；
  `SimulatedFrameSource` 供无音频端点的 CI 验证控制链路。
- `tools/capture-probe.ps1`：JSON 诊断报告（Windows 版本、设备、参数、统计、错误）。
- `scripts/capture-smoke.ps1` 重写为 PASS/SKIP/FAIL 三态；判定逻辑抽到 `scripts/capture-verdict.ps1` 并有 9 条自测。
- `scripts/capture-stability.ps1`：30 分钟采样 CPU/WorkingSet/PrivateBytes/帧率/丢帧/状态，输出 JSON+CSV。

### 实际执行与结果

| 命令 | 结果 |
|---|---|
| `uv run pytest -q` | **163 passed, 1 skipped**（新增 40：状态机、票据、控制面、控制消息） |
| `dotnet test services/audio-capture/tests -c Release` | **78 passed**（新增 4：状态序列化、ticket、配置） |
| `pwsh -File scripts/e2e-control.ps1 -Seconds 6` | **PASS：300 帧，peak -7.7 dBFS，idle，无进程残留** |
| `pwsh -File scripts/e2e-control.ps1 -Seconds 5 -Simulated` | **PASS：252 帧（静音源）** |
| `pwsh -File scripts/capture-smoke.ps1 -Seconds 5` | **PASS：247 帧，peak -9.7 dBFS** |
| `pwsh -File scripts/capture-verdict.ps1 -SelfTest` | **PASS：9 条判定用例** |
| `pwsh -File tools/capture-probe.ps1 -Seconds 3` | 输出 JSON：Windows 11 build 26300、1 渲染端点 + 1 采集端点、157 帧、peak -10.5 dBFS |
| `pwsh -File scripts/capture-stability.ps1 -Minutes 30 -SampleSeconds 10` | **完成：180 个采样点，90291 帧，50.06 fps，gap/invalid/duplicate/rateLimited 全 0，peak -4.4 dBFS，最终 idle** |

### 30 分钟稳定性数字

| 指标 | 值 |
|---|---|
| 时长 / 采样点 | 1800 s / 180 |
| 帧数 / 平均帧率 | 90291 / 50.06 fps |
| 丢帧 / 无效 / 重复 / 超速 | 0 / 0 / 0 / 0 |
| 峰值电平 | -4.4 dBFS |
| CPU 增长 | 27.47 s（约 1.5% 单核） |
| WorkingSet 增长 / 峰值 | +10.68 MB / 74.76 MB |
| PrivateBytes 增长 / 峰值 | +7.00 MB / 35.21 MB |
| 结束状态 | idle（capture.stop 正常） |

注意：测试期间机器同时跑了构建与其他测试，CPU 数字包含背景负载；WorkingSet 增长主要发生在启动阶段，
报告保留全部采样点（`.artifacts/stability/*.csv`）以便复现。

### 遇到的问题与修复

| 问题 | 原因 | 修复 |
|---|---|---|
| `capture.start` 立刻 `sidecar_idle` | sidecar 连接后抢先上报 idle，Core 处于 STARTING 时把它当作启动失败 | 控制模式不再在启动时上报 idle，只上报状态变化 |
| `/healthz` 500 | `requestId` 保存的是 pydantic `RequestId` RootModel，不可 JSON 序列化 | 存为 `str(root)`；healthz 增加异常兜底（记录日志并返回 `internal_error`） |
| `RTC_CAPTURE_SIDECAR` 路径被吞掉反斜杠 | `shlex.split` POSIX 模式把 `\` 当转义 | 非 POSIX 拆分，或接受 JSON 数组 |
| Windows 无端点 CI 无法跑控制 E2E | 托管 runner 没有音频设备 | 增加 `RTC_CAPTURE_SIMULATED=1` 的静音帧源，仍走真实进程与控制消息 |
| 冒烟把"采集失败"当 SKIP | 旧脚本对非零退出只打印 SKIP | 三态判定 + 错误码；设备存在时失败一律 FAIL |
| 判定逻辑无法在无设备环境验证 | 逻辑内联在脚本里 | 抽成 `capture-verdict.ps1`，9 条自测覆盖 |

### 未完成 / 风险

- 进程级 loopback 未实现（Phase 2-D）；当前只有能力探测位置预留。
- 设备热插拔只做"检测到变化→报错停止"，没有自动切换（符合"不静默切换"要求）。
- 30 分钟测试在本机（Windows 11 26300 + Senary 虚拟声卡）完成，未在目标会议室机器复测。
- 腾讯会议/微信/浏览器三场景实机记录仍缺。
- 票据不证明进程身份（见 `SECURITY.md §7`）。

### 下一步

Phase 2-D：进程级 loopback 能力探测与实现；之后 Phase 3 接 ASR。

### 变更记录
### 变更记录

| 日期 | 变更 |
|---|---|
| 2026-11-20 | 初始版本：完成 Phase 0 审查与全部设计文档 |
| 2026-11-20 | Phase 1：协议、Core、音频骨架、桌面骨架、CI、安全文档 |
| 2026-11-20 | Phase 2-A：音频二进制协议、Core 接收与统计、C# 发送与模拟流、60 秒端到端 |
| 2026-11-20 | Phase 2-B：WASAPI 设备级采集、重采样分帧、电平统计、真实采集冒烟 |
| 2026-11-20 | Phase 2-C：采集控制面、短期票据、三态冒烟、诊断与 30 分钟稳定性 |
