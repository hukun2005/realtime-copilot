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

## 2026-11-20 · P2-C.1：控制面并发与清理加固

分支：`feature/phase-2c1-controller-hardening`（基线 `6f4c560`）。

### 修复内容

- `CaptureController` 引入 `asyncio.Lock`：start/stop 的状态检查、迁移与会话登记全部串行化，
  **不在持锁期间等待进程退出或 sidecar 状态回报**。
- 会话代次（generation）：每次 start 递增；`handle_status` 校验连接实例、代次与 requestId，
  迟到/跨连接/跨会话消息直接忽略。
- 迟到状态语义：STARTING + idle → ERROR(`sidecar_idle`) 并清理；STOPPING + capturing 忽略；
  STOPPING + idle → IDLE；ERROR 不被迟到消息清除。
- 统一失败收尾 `_fail_and_cleanup`：取消超时任务、撤销票据、终止进程（幂等，只终止一次）、
  清空连接、广播最终状态；正常停止走 `_conclude_stop`，不会被误报为 ERROR。
- `attach` 改为 async，连接登记与 start 指令下发分离；`stop` 等待 `session.stopped` 事件而非轮询。
- `/healthz` 增加 `generation`、`sidecarExitCode`、`sidecarTermination`（`core`/`self`）。
- 脚本：稳定性报告记录 Windows build、状态迁移、sidecar 退出码、模拟/真实标记，失败时退出码非零；
  诊断报告补充 start/stop 状态与退出码。

### 实际执行与结果

| 命令 | 结果 |
|---|---|
| `uv run ruff check .` | All checks passed |
| `uv run pytest -q` | **181 passed, 1 skipped**（新增 18 个加固测试） |
| `dotnet test services/audio-capture/tests -c Release` | 78 passed（本轮未改 C#） |
| `pwsh -File scripts/e2e-control.ps1 -Seconds 5` | PASS：250 帧，peak -10.5 dBFS，idle，无残留 |
| `pwsh -File scripts/e2e-control.ps1 -Seconds 4 -Simulated` | PASS：201 帧（静音源） |
| `pwsh -File scripts/capture-stability.ps1 -Minutes 1 -SampleSeconds 2 -Simulated` | PASS：30 采样点、3162 帧、51.7 fps、0 丢帧，报告含 build 26300 与状态迁移 |
| `pwsh -File tools/capture-probe.ps1 -Seconds 3` | start=capturing、stop=idle、exit=1(termination=core)、161 帧 |

说明：正常停止后 Core 会主动终止 sidecar 进程，因此 `sidecarExitCode` 为 1，
`sidecarTermination=core` 表示"由 Core 终止"而非崩溃；报告同时记录该字段以避免误读。

### 新增测试（18）

- 并发：20 并发 start 只 spawn 一个；20 并发 stop 只下发一次且只终止一次；start/stop 交错；
  两个客户端同时 start；sidecar 连接期间重复 start。
- 迟到事件：STARTING+idle、STOPPING+capturing、STOPPING+idle、ERROR+capturing、ERROR+idle、
  旧连接状态、wrong requestId。
- 清理：启动超时、停止超时、sidecar error、shutdown、disconnect、崩溃与 stop 竞态。
- 断言真实副作用：spawn 次数、`terminate_calls`、票据 `consume` 失败、后台任务结束、`sidecarPid` 为空。

### 未完成 / 风险

- 本轮未改动 C# sidecar 与音频协议。
- 进程级 loopback、按来源统计、静音事件仍未实现（Phase 2-D / 后续）。
- 票据仍不证明进程身份。

## 2026-11-21 · P2-C.2：控制面最终加固

分支：`feature/phase-2c2-final-hardening`（基线 `8f96a3a`）。

### 修复内容

- **锁外创建 sidecar**：`start` 改为三阶段——持锁登记会话/代次/票据并进入 STARTING →
  锁外 `create_subprocess_exec` → 重新持锁确认会话仍有效；会话已关闭则锁外立即终止新进程。
  持锁期间不再等待进程创建。
- **任务绑定会话**：`timeout_task` / `watch_task` / `control_task` 移入 `_Session`；
  清理只取消本会话任务，且跳过当前正在执行的任务；停止时只取消启动超时，保留进程监看。
- **disconnect 竞态**：同一把锁内读取会话/连接/状态快照，锁外执行终止与广播；
  `_conclude_stop` / `_fail_and_cleanup` 再次校验会话身份与状态，幂等。
- **票据边界**：到期判定改为 `now >= expiry`（到期瞬间失效）；新增 512 字符上限；
  Base64 使用 `validate=True`（非法字符/长度直接拒绝）；畸形 JSON、旧会话、已结束会话、
  重放均返回统一 `TicketRejection`，不暴露签名或 secret。
- **文档**：SECURITY.md 清理"控制面占位/票据未实现/按进程采集在 Phase 2-C"等过期说法，
  统一为"票据已实现但不证明进程身份"。

### 实际执行与结果

| 命令 | 结果 |
|---|---|
| `uv run ruff check .` | All checks passed |
| `uv run pytest -q` | **199 passed, 1 skipped**（新增 18：spawn 窗口 3、会话任务 3、disconnect 竞态 2、票据边界 10） |
| `pwsh -File scripts/e2e-control.ps1 -Seconds 4` | PASS：200 帧，peak -10.5 dBFS，idle，无残留 |
| `pwsh -File scripts/e2e-control.ps1 -Seconds 4 -Simulated` | PASS：201 帧（静音源） |
| `dotnet test services/audio-capture/tests -c Release` | 78 passed（本轮未改 C#） |
| `pnpm -C packages/protocol test` / `pnpm -C apps/desktop test` | 22 / 11 passed |
| `pnpm -r --if-present run typecheck` | 通过 |

### 新增测试（18）

- `test_capture_session_lifecycle.py`：stop/shutdown 在 spawn 期间发生、spawn 失败清理、
  旧会话任务不影响新会话、shutdown 取消全部任务、disconnect 与 stop、disconnect 与进程退出、
  双重 stop 不重复终止。
- `test_tickets.py`（新增 10）：到期瞬间、到期前 1 秒、超长输入、畸形 Base64、畸形 JSON、
  已结束会话、新会话不能消费旧票据、重放、错误信息不泄露签名/secret。

### 未完成 / 风险

- 本轮未重跑 30 分钟稳定性（控制面改动不触及采集环路；上一次 30 分钟数据对应 P2-C 代码）。
- 票据仍不证明 Windows 进程身份；进程级 loopback、按来源统计、静音事件仍未实现。
- C# sidecar 未改动。

## 2026-11-21 · P3：ASR 协议与接口准备

分支：`feature/p3-asr-contract`（基线 `71a2a17`）。

### 协议（P3-A）

- 新增 `asr.status`、`asr.partial`、`asr.final`、`asr.error` 四个事件与对应 payload；
  `AsrSource`（loopback/mic）、`AsrState`、`AsrSegmentId` 独立定义，不改动音频二进制协议 v1。
- 四个合法 fixtures + 生成 TS 类型与 Pydantic 模型；`EVENT_TYPES` 从 8 增至 12。
- 错误码新增 `asr_failed` / `asr_timeout` / `asr_unavailable` / `asr_segment_conflict`。
- Python 侧新增 `make_asr_status` / `make_asr_result` / `make_asr_error` 构造函数。

### 状态与票据（P3-B/C）

- `AsrStatusTracker`：idle/connecting/ready/error，与采集状态机完全独立；ASR error 不影响采集。
- `TicketIssuer.issue(purpose="audio", generation=None)` / `consume(..., purpose, generation)`：
  audio 与 asr 票据隔离；asr 票据必须绑定 generation；过期、重放、旧会话、结束会话均拒绝；
  payload 仅含 `p/n/s/e/g`，不含任何 API Key。

### VAD（P3-D）

- `EnergyVad`（阈值判定）与 `Segmenter`（per-source 状态、有界 Ring Buffer、丢弃计数）。
- 事件：`speech_started` / `speech_ongoing` / `speech_ended`；loopback 与 mic 状态互不影响。
- 不缓存无上限音频：`ring_max_frames` 固定，超出即丢弃最旧帧并计数。

### 实际执行与结果

| 命令 | 结果 |
|---|---|
| `uv run ruff check .` | All checks passed |
| `uv run pytest -q` | **229 passed, 1 skipped**（新增 30：ASR 17、票据 5、协议 fixture 等） |
| `pnpm -C packages/protocol test` | 26 passed（fixtures 与事件类型扩展） |
| `pnpm -C packages/protocol typecheck` | 通过 |
| `uv run pytest packages/protocol/python/tests -q` | 35 passed |

### 未完成

- 真实 ASR provider、转写装配、`asr.status` 广播与 UI 接线、延迟评测、Silero VAD 均未实现。
- 本轮只冻结协议与接口，不宣称任何识别能力可用。

## 2026-11-21 · P2-D：按来源统计、Channel 并发与 30 分钟稳定性

分支：`feature/phase-2d-audio-reliability`（基线 `18613ee`）。

### 按来源统计（P2-D-B）

- `AudioSourceStats`：loopback / mic 分别维护 framesReceived、framesDropped、duplicates、
  invalid、peakDbfs、rmsDbfs、silenceDurationMs、lastFrameTimestamp、currentSampleRate、currentChannels。
- `AudioSession` 按 `frame.source` 更新对应来源；`AudioHub` 跨会话共享并汇总。
- 协议扩展：`audio.status.payload.sources`（可选，loopback/mic 各自统计），`/healthz.audio.sources` 同源。

### C# 并发模型（P2-D-C）

- 有界 `Channel<AudioFrame>`（容量 200）+ 单消费者；回调只 `TryWrite`，网络发送在独立任务。
- 满载策略：`BoundedChannelFullMode.Wait` 让 `TryWrite` 返回 false，丢弃新帧并计数（不阻塞回调）。
- 停止顺序：取消 → 采集源 `StopAsync` → 取消订阅 → 发送任务退出；重复取消/停止不崩溃。
- 新增测试：消费者阻塞时丢帧、取消后源恰好 Stop 一次、停止后 Emit 不抛异常。

### 稳定性报告（P2-D-A）

`scripts/capture-stability.ps1` 记录：Git SHA、sidecar 版本、采集模式、起止时间、
理论/实际帧数、丢帧/重复/无效、CPU/WorkingSet/PrivateBytes、状态迁移、sidecar 退出码；
判定 PASS 需要同时满足：最终 idle、时长足够、采样数 ≥ 90% 期望、最大采样间隔 ≤ 3×周期、
帧数偏差 ≤ max(200, 5%)。报告写入 `artifacts/capture-stability`。

### 实际执行与结果

| 运行 | 结果 |
|---|---|
| 模拟音频 30 分钟 | **PASS**：180 采样点、90320 帧、50.06 fps、0 gaps/duplicates/invalid/rateLimited、maxGap 10.1s、frameDelta 24、CPU +21.83s、WorkingSet +13.91MB、PrivateBytes +10.59MB、最终 idle |
| 真实 WASAPI loopback 30 分钟 | **PASS**：180 采样点、90305 帧、50.06 fps、peak -5.1 dBFS、rms -13.5 dBFS、silence 3.16s、0 gaps/duplicates/invalid/rateLimited、maxGap 10.1s、frameDelta 19、CPU +25.61s、WorkingSet +10.72MB、PrivateBytes +7.72MB、最终 idle |
| `uv run pytest -q` | **234 passed, 1 skipped** |
| `dotnet test services/audio-capture/tests -c Release` | **81 passed** |
| `uv run ruff check .` | All checks passed |

两次运行都绑定运行时 HEAD `18613ee`（本地未提交改动不包含在 SHA 中，这是报告口径的已知限制）。

### 未完成（明确标记）

- **设备热插拔自动恢复未实现**：没有 DEVICE_LOST/RECOVERING/ERROR 状态机、没有三次指数退避重试。
- **进程级 loopback 未实现**：`WithProcessLoopback` 能力探测与腾讯会议/微信实机验证均
  **NOT_VERIFIED**，本轮未做任何能力声明。
- 真实 30 分钟只在本机（Windows 11 26300 + Senary 虚拟声卡 + 测试音）完成，未在目标会议室机器复测。

## 2026-11-21 · P2-D.1：音频恢复与可靠性

分支：`feature/phase-2d1-audio-recovery`（基线 `96b1e47`）。

### 设备恢复（A2）

- 新增 `RecoveryPolicy`：最多 3 次，退避 500ms / 1s / 2s。
- 默认设备变化：停止旧源 → 重新枚举 → 新 `LoopbackCaptureSource` → 等待 `Started` → 广播新设备 ID；
  恢复期间广播 `starting` + `errorCode=recovering`，不会静默显示 capturing；3 次失败 → `error` + `device_unavailable`。
- 显式设备：轮询端点是否仍存在；消失时广播 `device_lost` 并停止，**不静默切换**成默认设备。
- 恢复失败/超时/设备消失的实机报告：**NOT_VERIFIED**（本机无法安全拔插虚拟声卡）。

### 来源级状态字段（A3）

`AudioSourceStats` 新增可选 `state` / `deviceId` / `recoveryAttempts` / `errorCode`，
使 `audio.status.sources` 能表达来源级状态；音频二进制协议 v1 未改动。

### FrameStreamer 清理（A4）

- 内部 `CancellationTokenSource`、`SemaphoreSlim` 生命周期锁；统一 `StopAsync`。
- 取消 → `Writer.TryComplete()` → 等待消费者 → 停止采集源；`RunAsync` finally 统一收尾。
- `FramesDropped` 改为 `Interlocked` 计数；`StopAsync` / `DisposeAsync` 幂等。
- 新增 5 个测试：启动前失败、发送失败后清理、多线程回调计数一致、Stop 与回调竞争、重复 Dispose。

### 稳定性报告可信度（A5）

新增 `gitSha`、`gitDirty`、`dirtyFiles`、`sidecarSha256`、`stabilityScriptSha256`、
`buildConfiguration`、`testMode`、`reproducibility`。工作区不干净时
`result=NOT_REPRODUCIBLE`，**不会标记 PASS**。已实测：脏工作区运行返回 NOT_REPRODUCIBLE。

### 进程级 loopback 探测（A6）

`audio-capture --probe-process-loopback [pid]` 输出 JSON：Windows Build、Build 是否 ≥20348、
NAudio 是否暴露 process-loopback API、目标 PID 是否有效；子进程/退出行为/真实音频/
腾讯会议/微信全部标记 **NOT_VERIFIED**。本机探测结果：build 26300、supported=true、apiPresent=true。

### 实际执行

| 命令 | 结果 |
|---|---|
| `dotnet test services/audio-capture/tests -c Release` | **91 passed** |
| `uv run pytest -q` | **234 passed, 1 skipped** |
| `pnpm -C packages/protocol test` | 26 passed |
| `uv run ruff check .` | All checks passed |
| `--probe-process-loopback` | 输出 NOT_VERIFIED 报告 |

### 未完成

- 真实设备拔插的自动化/实机报告（NOT_VERIFIED）。
- 腾讯会议、微信 PC 的进程级 loopback 实机验证（NOT_VERIFIED）。
- 干净工作区的 30 分钟稳定性报告未在本轮重跑；上一轮 30 分钟报告对应 `18613ee` 之前的代码。

## 2026-11-21 · P3.1：实时 VAD 接线

分支：`feature/p3-1-vad-runtime`（基线 `f31a86f`）。

### VAD 运行时

- `VadFrame` 保存完整帧（PCM、sequence、captureTsNs、采样率/声道、峰值/RMS）。
- Ring Buffer 有界，默认 2 秒 pre-roll；覆盖最旧帧单独计 `ringBufferEvictions`。
- 片段包含 pre-roll 与起始确认阶段的帧；结束时间取最后一帧有效语音，结束静音单独记录。
- `maxSegmentMs` 超长切分；loopback/mic 各自独立状态。

### Core 接线

- `AsrPipeline`：每来源有界帧队列（默认 200）+ 有界 Segment 队列（默认 8）；
  非阻塞 `ingest`；队列满计数 `framesDropped` / `segmentsDropped`；
  VAD 异常计数但不断开采集；停止时 flush 剩余片段。
- `AudioSession` 在帧被接受后把 PCM 交给 pipeline；VAD 入队异常不冒泡到 WebSocket。
- `AudioHub` 暴露 `vad` 快照；app 在 lifespan 启动/停止 pipeline，并广播 `asr.status`（idle）。
- `AsrStreamTracker` 已完成片段按 FIFO 淘汰（默认 512），避免无限增长。

### 测试与 fixture

- 新增 `tests/fixtures/vad-sample.wav`：合成 PCM（1s 静音 + 1s 440Hz + 0.5s 静音），
  非真实会议音频。
- 新增 15 个测试：纯静音、连续语音、短噪声、pre-roll、结束静音、停顿切分、超长切分、
  双来源隔离、时间戳倒退、Ring Buffer 上限、ASR 队列满、stop flush、VAD 异常、
  真实 PCM 片段一致性、已完成段缓存上限。

### 实际执行

| 命令 | 结果 |
|---|---|
| `uv run pytest -q` | **242 passed, 1 skipped** |
| `uv run ruff check .` | All checks passed |
| `dotnet test services/audio-capture/tests -c Release` | 91 passed（本轮未改 C#） |
| `pnpm -C packages/protocol test` | 26 passed |

### 未完成

- 真实 ASR Provider、`asr.partial/final` 的实际产出与广播（仅冻结协议与运行时队列）。
- Silero VAD、ASR 延迟评测。
- 真实会议音频 fixture（按要求不入库）。

## 2026-11-21 · P3.1.1：VAD 质量与内存加固

分支：`feature/p3-1-1-vad-hardening`（基线 `90a6339`）。

### PCM 统计口径（A1）

- `Segmenter._finish_segment` 的 peak / RMS 改为直接基于最终 `pcm16`（`copilot_core.pcm_level`），
  与采集侧电平定义一致；结束静音不进入最终 PCM，仍记录 `endSilenceMs`。

### Energy VAD（A2）

- Peak + RMS 双指标；RMS 过低但峰值高的帧计为 `noise_triggers`，不当作语音。
- 背景噪声 EMA 估计与自适应阈值（噪声底 + 余量），可关闭。
- 起止滞回：激活后阈值降低 `hysteresis_db`，避免抖动。
- 旧构造参数 `threshold_dbfs` 与 `is_speech(dbfs)` 保持兼容。

### Provider 抽象（A3）

- `VadProvider` 协议 + `EnergyVadProvider`（默认）；`WebRtcVadProvider` 依赖可选 `webrtcvad`，
  未安装时抛 `VadUnavailable`，不影响默认路径；Silero 只保留扩展位置。

### PCM 预算与生命周期（A4/A5）

- `AsrPipeline` 新增：`maxSegmentBytes`、`maxPcmBytesInFlight`（默认 32 MiB）、
  `maxSegmentDurationMs`，以及 `pcmBytesInFlight` / `peakPcmBytesInFlight` /
  `pcmBytesDropped` / `pcmBytesReleased` 统计。
- 单段超限、全局预算超限、队列满三种情况均确定性丢弃并计数。
- `next_segment` / `get_segment` 取出即释放预算；`stop_immediate` / `close` 清空并释放全部 PCM。
- 生命周期：`start` / `stop_graceful` / `stop_immediate` / `close`；幂等、取消全部 worker、
  无后台 Task 残留；worker 异常计入 `workerErrors` 并通过 healthz 暴露。
- `/healthz.audio.vad` 输出上述计数。

### 测试

新增 14 个：PCM 口径、低音量、冲击噪声、稳态噪声、RMS 拒绝、滞回、Provider 包装、
WebRTC 不可用、单段预算、全局预算、消费释放、优雅停止、立即停止、close 幂等。

| 命令 | 结果 |
|---|---|
| `uv run pytest -q` | **256 passed, 1 skipped** |
| `uv run ruff check .` | All checks passed |
| `dotnet test services/audio-capture/tests -c Release` | 91 passed（本轮未改 C#） |
| `pnpm -C packages/protocol test` | 26 passed |

### 未完成的长时间测试（A6）

- 30 分钟模拟、30 分钟真实 WASAPI、60 分钟真实 WASAPI、真实双来源并行：
  **本轮未执行**，不标记通过。
- 队列满载、ASR 消费不足、取消、设备断开由单元测试覆盖，未做长时间 soak。

## 2026-11-21 · P3.2：真实 ASR Provider 运行时

分支：`feature/p3-2-asr-runtime`（基线 `d5914c5`）。

### Provider 抽象与注册表

- `AsrProvider` 协议：`connect` / `transcribe` / `cancel` / `close` / `status`，
  结果用 `AsrResult`（segmentId、source、revision、text、时间戳、final、confidence）。
- `AsrProviderConfig`：语言、采样率、声道、模型、API Key 环境变量名。
- `AsrRegistry`：按名字创建 Provider；已注册 `deepgram`。

### AsrManager

- 状态 IDLE/CONNECTING/READY/ERROR，独立于采集状态机；每次变化广播 `asr.status`。
- 启动时签发 `purpose=asr`、绑定 generation 的一次性票据并消费；generation 不符拒绝启动。
- loopback / mic 各一个串行消费者；每段有超时；停止取消消费者并关闭 Provider。
- 结果经 `AsrStreamTracker` 校验：revision 单调、final 后拒绝旧 partial、同段只 final 一次；
  冲突以 `asr.error(asr_segment_conflict)` 广播。
- 旧会话/已停止后的迟到结果不广播。

### 真实 Provider（Deepgram，未验证）

- `DeepgramProvider`：WebSocket `wss://api.deepgram.com/v1/listen`，PCM16/16k/单声道，
  `interim_results=true`；API Key 仅从环境变量读取。
- **本机无 API Key，未完成真实连接/识别验证，标记 NOT_VERIFIED。**
- 每个 Segment 建立独立连接（实现简单、可审计），未做连接复用。

### 事件广播

`asr.status` / `asr.partial` / `asr.final` / `asr.error` 通过 Core 现有 broadcast 发送；
Provider 不直接接触 WebSocket。`/healthz.asr` 暴露状态快照；`RTC_ASR_PROVIDER` 未设置时保持 IDLE。

### 测试

新增 14 个：启动/票据消费、partial+final、final 后旧 partial、超时、连接失败、认证失败、
旧 generation 拒绝、双来源、stop 幂等、取消、停止后迟到结果、Provider 注册、转写失败。
Provider 全部使用 Mock；真实网络路径未测。

| 命令 | 结果 |
|---|---|
| `uv run pytest -q` | **270 passed, 1 skipped** |
| `uv run ruff check .` | All checks passed |
| `dotnet test services/audio-capture/tests -c Release` | 91 passed（本轮未改 C#） |
| `pnpm -C packages/protocol test` | 26 passed |

### 未完成（真实识别）

- 真实 Provider 连接、真实 PCM 识别、Partial/Final 延迟、WER、成本、内存峰值：
  **NOT_VERIFIED**（无 API Key，未执行）。
- 真实会议音频评测语料未建立（按要求不上传隐私音频）。

## 2026-11-21 · P3.2.1：ASR 正确性修复

分支：`feature/p3-2-1-asr-correctness`（基线 `1390355`）。

### Deepgram final 累积（A1/A2）

- `is_final` 文本块累积到 utterance；`speech_final` 才产出 final；空文本事件安全忽略。
- 每个 Segment 独立 revision：每次 partial 递增，final 使用最新 revision；不同 Segment 互不影响。
- `speech_final` 后短暂 drain 剩余消息；CloseStream 后到达的结果仍会被处理。
- 提前关闭映射 `asr_connection_lost`，接收超时映射 `asr_timeout`，空 PCM/格式错误映射 `asr_invalid_audio`。

### 会话级生命周期（A3/A4）

- 新增 `AsrCoordinator`：Core 启动时 ASR 为 IDLE；`capture.start` 后按 generation 创建 Manager；
  `capture.stop`/失败收尾停止 Manager 并先把 generation 置 0，旧结果无法广播。
- `CaptureController` 通过 `on_capture_start/on_capture_stop` 回调触发，不复制采集状态机；
  ASR 回调异常只记录日志，不影响采集。
- Manager 在每次发布前校验 active、generation、segmentId、source；旧 generation 结果拒绝并广播 `asr_stale_generation`。

### Provider 取消与清理（A5）

- Deepgram 维护活动 socket 集合；`cancel`/`close` 关闭连接，幂等；取消后不再发布结果。

### PCM 预算（A6）

- 预算拆成 `pcmBytesQueued` + `pcmBytesProcessing`；`get_segment` 只标记 processing，
  ASR 在成功/失败/取消/超时后调用 `release_segment`（幂等）。
- `total = queued + processing` 参与全局预算判断；close 释放全部。

### 状态与错误（A7）

- 错误码新增并区分：`asr_auth_missing`、`asr_rate_limited`、`asr_connection_lost`、
  `asr_invalid_audio`、`asr_provider_error`、`asr_stale_generation`。
- 单段失败只广播 `asr.error`，不把 Provider 整体标为断开。

### 测试

新增 19 个：Deepgram final 累积/revision/超时/提前关闭/取消（11）、Coordinator 生命周期与旧会话隔离（6）、
PCM processing 预算（2）。

| 命令 | 结果 |
|---|---|
| `uv run pytest -q` | **289 passed, 1 skipped** |
| `uv run ruff check .` | All checks passed |
| `dotnet test services/audio-capture/tests -c Release` | 91 passed（本轮未改 C#） |
| `pnpm -C packages/protocol test` | 26 passed |

### 未完成

真实 Deepgram 连接与识别验证不在本 PR（PR B）；30/60 分钟长稳未执行。

## 2026-11-21 · P3.2.2：Deepgram 真实识别验证

分支：`feature/p3-2-2-deepgram-validation`（基线 `0351e14`）。

### 官方接口核对

- 参考：https://developers.deepgram.com/reference/speech-to-text/listen-streaming
  与 https://developers.deepgram.com/docs/understand-endpointing-interim-results
- 参数：`model`（示例 `nova-3`）、`language`、`encoding=linear16`、`sample_rate`、`channels`、
  `interim_results`、`endpointing`（毫秒，触发 `speech_final`）、`punctuate`、`smart_format`、
  `CloseStream`。全部可通过 `AsrProviderConfig` / `options` 配置。
- `is_final` 表示文本块定稿；`speech_final` 表示 utterance 结束（与 P3.2.1 实现一致）。

### 输入校验

- 仅接受 PCM16LE / 16000Hz / Mono；采样率或声道不符返回 `asr_invalid_audio`。
- 空 PCM 返回 `asr_invalid_audio`；连接/接收超时可配置。
- API Key 仅从 `DEEPGRAM_API_KEY` 读取，不写入日志、不进入仓库。

### 真实 API 测试

- `scripts/asr-live-check.ps1` + `scripts/asr_live_check_impl.py`：本地合成 2 秒 PCM，
  有 Key 时执行一次 Segment 识别并输出 partial/final 与延迟；无 Key 输出 SKIP。
- 本机执行结果：**SKIP**（`DEEPGRAM_API_KEY is not set`）。
- `tests/test_deepgram_live.py`：缺少 Key 时整体 skip；本机未执行真实断言。

### 30 分钟联合测试

- 模拟音频 + VAD、真实 WASAPI + VAD、真实 WASAPI + Deepgram、双来源并行：
  **NOT_RUN**（无 API Key，且本轮未安排长跑）。
- 未用 Mock 结果冒充真实识别。

### 未解决

- 真实连接成功率、认证失败、断网重连、识别质量（WER）、延迟分布、成本：
  **NOT_VERIFIED / NOT_RUN**。

## 2026-11-21 · P3.2.3：ASR 生命周期竞态与 Deepgram 可靠性

分支：`feature/p3-2-3-asr-lifecycle-hardening`（基线 `3697b44`）。

### 生命周期时序

```
capture.start(N)
  → controller._notify_asr_start(N) [task]
    → coordinator.start(N): epoch+1, generation=N, 创建 Manager（锁外连接）
      → manager.start(): 票据 → provider.connect()（验证 socket）→ READY
capture.stop()
  → controller._notify_asr_stop() [task，排在 start 之后]
    → coordinator.stop(): epoch+1, generation=0, manager.stop()
      → 取消消费者 → provider.close() → IDLE
  → start 返回后校验 epoch/generation：已过期则立即 manager.stop()（幂等）
```

- Controller 用任务集合跟踪 ASR 生命周期；shutdown 禁止新 start 并 join 全部任务。
- Coordinator 用 epoch + generation 双校验；stop 不等待正在进行的连接，但旧 start 完成后必定自毁。
- `AsrCoordinator.shutdown()` 取消并等待所有生命周期任务，关闭 Manager 与 Provider。

### Deepgram 状态与连接

- 状态：IDLE → CONNECTING →（轻量验证 socket 成功）→ READY；失败 → ERROR；close 幂等。
- 验证连接失败不会报告 READY；认证失败（401/403）不重试。
- 429/5xx 使用有限指数退避 + 抖动（最多 `max_retries` 次），不阻塞采集主循环。
- 连接/段/重连/限流/超时/Provider 错误均有计数，`/healthz.asr.metrics` 可查。

### Drain 修复

- `speech_final` 后进入 settle 窗口，继续解析迟到消息并累积有效 `is_final` 文本，
  Metadata/关闭/settle 超时后只产出一个 final；不再丢弃消息。
- 总结束等待有上限（`settle_timeout_seconds`，默认 0.5s）；取消会关闭 socket。

### 内存与观测

- PCM 预算区分 queued / processing / in-flight / peak（单元测试覆盖）；不代表进程内存。
- 新增 `memory.process_memory()`（Windows WorkingSet/PrivateBytes）并挂到 `/healthz.process`。
- ASR 快照包含 `activeTasks` 与 Provider 指标（含 activeWebSockets）。
- 新增 `scripts/asr_soak_impl.py`（模拟 VAD soak，输出 PCM 与进程内存）；**本 PR 未运行 30/60 分钟**。

### 测试

新增 21 个：生命周期竞态 8、Deepgram 状态/URL/Drain/指标 12、进程内存 1。
| 命令 | 结果 |
|---|---|
| `uv run pytest -q` | **310 passed, 3 skipped** |
| `uv run ruff check .` | All checks passed |
| `dotnet test services/audio-capture/tests -c Release` | 91 passed |
| `pnpm -C packages/protocol test` | 26 passed |
| `pnpm -C apps/desktop test` | 11 passed |
| `pnpm -r --if-present run typecheck` | 通过 |

### 未完成

- 30/60 分钟真实 WASAPI / Deepgram 联合测试：**NOT_RUN**。
- 真实 API Key 验证：**SKIP**（本机无 Key）。
- 持续实时流式 ASR（P3.3）：未开始。

## 2026-11-21 · P3.2.4：阿里云百炼 Paraformer 实时 ASR 接入

分支：`feature/p3-2-4-aliyun-paraformer`（基线 `d68cfc9`）。

### Provider

- 新增 `DashScopeParaformerProvider`：百炼 Paraformer 实时 WebSocket，
  `wss://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference`。
- 协议：`run-task` → 二进制 PCM → `finish-task`；解析 `result-generated.sentence`，
  `sentence_end=false` 为 partial，`true` 为 final。
- `connect()` 用 100ms 静音做真实握手验证，成功后才 READY；401/403 映射认证失败。
- 注册名 `aliyun`；配置：`RTC_ASR_PROVIDER=aliyun`、`DASHSCOPE_API_KEY`、
  `DASHSCOPE_WORKSPACE_ID`、可选 `RTC_ASR_MODEL`。

### 真实 API 验证

- 本机 Key + `ws-l262igu3wrsns7ns` 业务空间，`paraformer-realtime-v2`。
- `scripts/aliyun-live-check.ps1` 结果：**PASS**（连接 1330ms，9.26s 音频，15 partial / 2 final）。
- 识别文本：
  - final 1：`大家好，这是一次实时语音识别的测试。`
  - final 2：`今天讨论五三和TypeScript的接口设计。`
- 已知识别误差：`Vue3` → `53/五三`，`TypeScript` → `type escript`（模型对中英混合技术词有限制）。
- 测试音频为 Windows 中文 TTS 合成，**非会议录音**。

### 测试

| 命令 | 结果 |
|---|---|
| `uv run pytest -q` | **318 passed, 3 skipped** |
| `uv run ruff check .` | All checks passed |
| `dotnet test services/audio-capture/tests -c Release` | 91 passed |
| `pnpm -C packages/protocol test` | 26 passed |
| `pnpm -C apps/desktop test` | 11 passed |
| `pnpm -r --if-present run typecheck` | 通过 |

### 未完成

- 30 分钟真实 WASAPI + Paraformer 联合测试：**NOT_RUN**。
- 真实会议音频、WER、P95 延迟未测。
- 热词/自定义词汇表未接入（可改善 Vue3/TypeScript 识别）。

## 2026-11-21 · P3.2.5：真实 WASAPI soak 准备与 30 分钟尝试

分支：`feature/p3-2-5-wasapi-soak-harness`（基线 `0cd1992`）。

### 新增

- `scripts/asr-soak-wasapi.ps1`：启动真实 Core + sidecar，播放本地 TTS，控制面 `capture.start`，
  周期采样 `/healthz`，输出 `artifacts/asr-soak/wasapi-soak.json`。
- `scripts/asr_soak_wasapi.py`：控制面驱动与报告（SHA/dirty/Windows Build/PCM/进程内存/ASR 计数）。
- `scripts/play-loop.ps1`：循环播放测试语音。
- `AsrManager` 快照新增 `partials` / `finals` / `errors` 计数。
- App 按 provider 选择 Key 环境变量（aliyun → `DASHSCOPE_API_KEY`）。

### 30 分钟尝试结果与根因修正

首次冒烟失败（sidecar 启动后立即 `capture.stop`）的**根因是测试脚本**：

- `scripts/asr-soak-wasapi.ps1` 的 `Minutes` 参数声明为 `[int]`；
- 传 `-Minutes 0.2/0.3` 被取整为 `0`，驱动等待 0 分钟并立即发送 `capture.stop`；
- sidecar 行为正常，并非产品生命周期缺陷。此前“sidecar 控制模式缺陷”的结论已更正。

修复：`Minutes` 改为 `[double]`；驱动增加心跳 `pong` 应答；Aliyun Provider 把同一 Segment 的
多个 `sentence_end` 合并为一个 final，避免 `asr_segment_conflict`。

1 分钟真实 WASAPI + Paraformer 冒烟（干净工作区外，验证链路）：**PASS**
（2507 帧 / 14 Segment / 119 partial / 14 final / 0 error）。

30 分钟完整报告在本次提交后、干净 SHA 上单独执行；未完成前仍标记 **NOT_RUN**。

### 其他验证

- 阿里云 Provider 真实 API 自检已在 P3.2.4 完成：**PASS**（15 partial / 2 final）。
- Python **318 passed / 3 skipped**；ruff 通过。

### 未完成

- 30 分钟/60 分钟真实 WASAPI 联合测试：**NOT_RUN**。
- 真实会议音频、WER、P95 延迟：未测。

## 2026-11-21 · P3.2.6：30 分钟真实 WASAPI + Paraformer 结果

执行：`scripts/asr-soak-wasapi.ps1 -Minutes 30 -Interval 30`

| 项 | 值 |
|---|---|
| 结果 | **PASS** |
| Git SHA | `7e1f8a66f3f9f5af6b97aa7a039ee8de6e135df5` |
| 工作区 | 干净（`gitDirty=false`） |
| Windows Build | 10.0.26300 |
| 模型 | `paraformer-realtime-v2` |
| 音频 | 真实 WASAPI loopback（系统播放 TTS 语音，非会议录音） |
| 帧数 | 88,544（约 49.2 fps，理论 90,000，98.4%） |
| 丢帧 / 重复 / 非法 | 0 / 0 / 0 |
| VAD Segment | 513（丢弃 0） |
| ASR Partial / Final | 4,546 / 513 |
| ASR 错误 | 0 |
| ASR 状态 | ready |
| 连接尝试 / 失败 | 514 / 0 |
| PCM 峰值 in-flight | 350,080 bytes（≈342 KiB，预算 32 MiB） |
| 进程内存 | WorkingSet 65,515,520 B；PrivateBytes 45,936,640 B；Peak WS 67,026,944 B |

### 限制

- 音频是 Windows 中文 TTS 循环播放，不是真实会议；未测 WER。
- 未采集逐 Segment 的 P50/P95 识别延迟（只有连接耗时聚合与 partial/final 计数）。
- 仅 loopback 单来源；mic 双来源并行未跑。
- 60 分钟测试未执行。

## 2026-11-21 · P3.2.7：ASR 延迟分位与双来源

- Aliyun Provider 记录每 Segment 的首次 partial 与 final 延迟，输出 P50/P95（`latencySamples` 上限 5000）。
- `AsrManager` 快照增加 `bySource`（loopback/mic 各自的 partial/final 计数）。
- soak 脚本支持 `-Mic`：`capture.start` 的 `mic.enabled=true`，报告双来源帧数与 ASR 计数。
- 1 分钟双来源冒烟：loopback 14 final / mic 1 final，0 错误；final P50 1264.9ms / P95 1413.1ms。

## 2026-11-21 · P3.2.8：60 分钟双来源真实 WASAPI + Paraformer

执行：`scripts/asr-soak-wasapi.ps1 -Minutes 60 -Interval 60 -Mic`

| 项 | 值 |
|---|---|
| 结果 | **PASS** |
| Git SHA | `c26edb3445a9af1c93b01fe7920795d12a608ccb` |
| 工作区 | 干净（`gitDirty=false`） |
| Windows Build | 10.0.26300 |
| 模型 | `paraformer-realtime-v2` |
| 音频 | 真实 WASAPI loopback + mic（扬声器播放 TTS，mic 拾取） |
| 时长 | 60 分钟（60 个采样点） |
| 帧数 | loopback 177,059；mic 177,057（各 ~49.2 fps） |
| 丢帧 / 重复 / 非法 | 0 / 0 / 0 |
| VAD Segment | 1,993（丢弃 0） |
| ASR Partial / Final | 10,222 / 1,374 |
| 按来源 | loopback 9,219 partial / 1,025 final；mic 1,003 / 349 |
| ASR 错误 | 0 |
| 连接尝试 / 失败 | 1,994 / 0 |
| 首次 partial 延迟 | P50 870.5 ms / P95 1,138.6 ms |
| Final 延迟 | P50 1,217.2 ms / P95 1,791.5 ms |
| PCM 峰值 in-flight | 1,251,200 B（≈1.2 MiB，预算 32 MiB） |
| 进程内存 | WorkingSet 46.0 MB；PrivateBytes 48.4 MB；Peak WS 78.6 MB |

### 限制

- TTS 循环音频，不是真实会议；未测 WER。
- mic 拾取的是扬声器外放声音，不是独立说话人；双来源隔离已由计数验证，但非真人双讲。
- 每次 Segment 一条 WebSocket（1994 次连接），未做连接复用。
- 未采集端到端字幕延迟（只有 Segment 内首次 partial / final 延迟）。

## 2026-11-21 · P3.2.9：ASR 统计修复与阶段状态统一

分支：`feature/p3-2-9-asr-metrics-docs`（基线 `7e35b12`）。

### 修复

- `AsrManager._publish_error()` 中 `error_codes` 被重复累加（一次错误计两次），
  导致 60 分钟报告中 `errors=45` 而 `errorCodes=90`。
- 现在一次错误只增加 `errors +1`、对应错误码 `+1`；先计数再广播，
  广播失败仍保留“错误已发生”的计数。

### 指标口径（Aliyun Paraformer）

| 指标 | 口径 |
|---|---|
| `connection_attempts` | 每个 Segment 的握手尝试次数（含验证连接） |
| `connection_successes` / `failures` | 握手成功/失败次数；失败不计成功 |
| `connection_duration_ms` | 仅 WebSocket 握手耗时，不含音频发送与识别等待 |
| `segment_count` | 进入 transcribe 的 Segment 数 |
| `reconnect_count` | 仅 429/5xx 重试次数，普通首次连接不计 |
| `rate_limited_count` / `timeout_count` / `provider_error_count` | 对应错误路径次数 |
| `active_web_sockets` | 当前未关闭的 socket 数；正常/取消/超时/异常路径都会归零 |
| `latencySamples` | 有 final 的 Segment 数（上限 5000） |
| `firstPartialP50Ms/P95Ms` | Segment 内首次 partial 相对 transcribe 开始的延迟 |
| `finalP50Ms/P95Ms` | Segment 内 final 相对 transcribe 开始的延迟 |

这些是 **Segment 级识别延迟**，不是端到端面试回答延迟。

### 新增测试（11 个）

错误单次/连续/不同码计数、snapshot 一致性、广播失败计数、bySource、
失败连接不增加成功、验证连接后 active 归零、超时释放 socket、
正常路径无重连、连接耗时只含握手。

### 结果

Python **330 passed / 3 skipped**；ruff 通过；C# 91；协议 26；桌面 11；typecheck 通过。

### 未完成

- 持续实时流式 ASR（P3.3）：未开始。
- 真实会议 WER：未测量。
- 腾讯会议 / 微信 PC：NOT_VERIFIED。

## 2026-11-22 · P3.2.10：Aliyun 握手验证失败处理

基线 `115cfaa`；分支 `feature/p3-3-paraformer-connection-reuse`（P3.2.10 先单独提交）。

### 修复

- `connect()` 仅在收到 `task-finished` 后进入 READY。
- `task-failed` → `AsrError`，保留服务端 `error_code` 与安全消息；认证类错误映射 `asr_auth_failed`，
  限流类映射 `asr_rate_limited`，其余 `asr_provider_error`。
- 接收超时 → `asr_timeout`；socket 断开 → `asr_connection_lost`；未知/畸形事件 → 协议错误。
- `CancelledError` 向上传播，状态回到 idle。
- 所有路径 finally 关闭 socket；失败后状态为 error，`active_web_sockets` 归零。

### 测试

新增 8 个：正常结束、task-failed（含错误码保留）、认证失败、超时、socket 断开、
未知事件、畸形消息、connect 取消。

Python **338 passed / 3 skipped**；ruff 通过。

## 2026-11-22 · P3.3：Paraformer WebSocket 连接复用

基线 `8f6458e`（P3.2.10 合并后）。

### 官方协议依据

- 阿里云文档明确 `run-task` / `finish-task` 支持串行复用连接：
  https://help.aliyun.com/zh/model-studio/speech-recognition-best-practices
- 同一连接不并行任务；任务失败/网络异常后应废弃连接；空闲约 60 秒可能被服务端关闭。

### 实现

- 每来源一个 `_Session`（socket + last_used + task 数）与一把来源锁。
- 同一来源 FIFO 串行：`run-task → task-started → PCM → finish-task → task-finished` 后才执行下一 Segment。
- `task_id` 每任务重新生成；结果仍按 Segment 输出 partial/final（多句合并为一个 final）。
- 空闲超过 `idle_timeout_seconds`（默认 50s）在下次任务前重建连接。
- 失败：关闭并废弃连接；若尚未产出任何结果，用新连接重试当前 Segment 一次；
  已产出 partial 则不重试，交由 Manager 发 `asr.error`。
- `close()`/`cancel()` 关闭所有来源连接，幂等；`active_web_sockets` 归零。
- 指标新增 `connections_created`、`connections_reused`、`idle_closes`、`tasks_completed`。
- `reuse_connections=False` 保留旧的每 Segment 建连模式，用于 A/B 对比。

### 真实 API（本机 Key）

`scripts/aliyun_reuse_check.py`，3 个 Segment：

| 模式 | 连接创建 | 任务 | Final | 总耗时 | Final P50 | P95 |
|---|---|---|---|---|---|---|
| 复用 | 1 | 3 | 3 | 3.1s | 330.5ms | 489.0ms |
| 每段建连 | 0（3 次 attempt） | 0 | 3 | 5.8s | 741.7ms | 865.6ms |

### 稳定性测试（连接复用）

| 项 | 30 分钟单来源 | 60 分钟双来源 |
|---|---|---|
| Git SHA | `15d3333` | `66b94ba` |
| 工作区 | 干净 | 干净 |
| 帧数 | 88,553（loopback） | 177,039（loopback）+ mic |
| 墙钟 | 30 分钟 | 3,603.1s（ratio 1.001） |
| Segment / Final | 513 / 513 | 1,859 / 1,196 |
| Partial | 4,611 | 9,563 |
| ASR 错误 | 0 | 0 |
| 连接尝试 | **2**（验证 + 1） | **3**（验证 + loopback + mic） |
| Final P50/P95 | 380.8 / 803.8 ms | 353.3 / 761.2 ms |
| PCM 峰值 | 254,080 B | 449,920 B |
| 进程内存 | WS 66.7 MB / Private 48.8 MB | WS 68.6 MB / Private 50.1 MB / Peak 69.6 MB |

对比旧实现：30 分钟需 514 次连接；60 分钟双来源需 1,994 次连接。复用后分别降到 2 / 3 次。

说明：首次 60 分钟运行因机器休眠只采集到约 12 分钟，报告已作废；修复为 `SetThreadExecutionState` 防休眠 +
墙钟/帧率校验后重跑，上表为有效结果。TTS 音频，非真实会议；无 WER。

## 2026-11-22 · P34-01：TranscriptAssembler

基线 `cc6c6d2`；分支 `feature/p3.4-transcript-p3.5-evaluation`。

### 实现

- 新增 `copilot_core/transcript/assembler.py`：纯 Python，不依赖 FastAPI/WS/SQLite。
- 每来源待装配窗口（默认 3），按 `start_ts_ns` 排序；窗口溢出先产出最早一条，缓冲有界。
- 重复 `(generation, segment_id)` 去重；旧 generation 计数 `stale` 并丢弃。
- 相邻/重叠 Segment 在 `merge_gap_ms`（默认 200ms）内合并；中文直接拼接，英文/数字保留空格。
- 空文本与纯标点计入 `empty`，不进入缓冲。
- 大间隙（>2s）只计数 `gaps`，不插入占位文本。
- `flush()` 产出全部剩余缓冲；`reset(generation)` 清空会话状态。
- 统计：accepted/emitted/duplicates/out_of_order/merged/gaps/stale/empty/overflow_emitted。

### 测试

新增 12 个：排序、重复、乱序、相邻合并、英文空格、空/纯标点、generation 隔离、
reset、缓冲上限、双来源、gap、revision/provider 保留。

Python **361 passed / 3 skipped**；ruff 通过。

## 2026-11-22 · P34-02：TranscriptBuffer

基线 `e7de900`；分支 `feature/p3.4-transcript-p3.5-evaluation`。

### 实现

- 新增 `copilot_core/transcript/buffer.py`：每来源有界滚动缓冲，纯 Python。
- 双上限：每来源 Segment 数（默认 500）与总字符数（默认 200,000）；超限按最早
  `startTsNs` 淘汰，返回被淘汰 Segment 并计数。
- 重复 `segment_id` 幂等忽略；旧 generation 丢弃。
- gap：相邻 Segment 间隔超过阈值（默认 2s）计数并记录有界 `GapRange`（最多 100 条）。
- 乱序 Segment 仍按 `startTsNs` 插入并计 `out_of_order`。
- `snapshot(source=...)` 返回时间线/单来源只读副本；`recent_text(max_chars)` 取尾部上下文。
- `clear()` 保留 generation；`reset(generation)` 清空并切换会话。
- 统计：appended/duplicates/stale/out_of_order/gaps/evicted_segments/evicted_chars。

### 测试

新增 12 个：排序、双来源、重复、旧 generation、条数淘汰、字符淘汰、gap、
乱序、上下文尾部、clear/reset、空文本、快照副本。

Python **373 passed / 3 skipped**；ruff 通过。

## 2026-11-22 · P34-03：SQLite v2 与 TranscriptStore

基线 `0a4c131`；分支 `feature/p3.4-transcript-p3.5-evaluation`。

### 迁移

新增 v2（不修改 v1）：

- `meeting_sessions(id, generation, started_at, ended_at, mode, status)`
- `transcript_segments(id, session_id FK, generation, segment_id, source, start_ts_ns,
  end_ts_ns, text, revision, provider, created_at)`
- `UNIQUE(session_id, segment_id)`
- 索引 `(session_id, start_ts_ns)`、`(session_id, source, start_ts_ns)`
- `ON DELETE CASCADE`：删除会话即删除其转写

不存原始音频、不存 API Key；升级保留 v1 `settings`。

### Store

- `TranscriptStore.start_session` 幂等；`end_session` 更新状态/结束时间。
- `append` 用 `ON CONFLICT DO NOTHING` 幂等，返回是否插入；会话不存在抛 `StorageError`。
- `segments(source=, limit=)` 按 `start_ts_ns` 排序；`sessions`、`segment_count`、`delete_session`。
- 短事务同步写入，不阻塞在采集回调（由调用方在事件循环中按需使用）。

### 测试

新增 9 个：v2 表/索引、v1 升级保留 settings、session 幂等、写入/读取顺序、重复、
来源过滤/limit、无会话报错、结束/级联删除、无音频列。

Python **382 passed / 3 skipped**；ruff 通过。

## 2026-11-22 · P34-04：transcript 协议与事件整合

基线 `6d47460`；分支 `feature/p3.4-transcript-p3.5-evaluation`。

### 协议

新增两个事件（向后兼容，不改 `asr.*` 与音频 v1）：

- `transcript.segment`：`sessionId/segmentId/source/startTsNs/endTsNs/text/revision/generation/provider`
- `transcript.reset`：`sessionId/generation/reason(capture_stopped|generation_changed|error|shutdown)`

同步更新：

- `schema/events.v1.json`（`$defs` + `oneOf`）
- 生成 `src/generated/types.ts`、`python/copilot_protocol/models.py`
- `EVENT_TYPES`（TS/Python）
- fixtures：`transcript.segment.json`、`transcript.reset.json`

### Handler

新增 `transcript/handler.py`：

- `handle(asr.final)` → `TranscriptAssembler` → `TranscriptBuffer` → `TranscriptStore` → 广播 `transcript.segment`。
- 只处理 final；partial/status/error 忽略。
- 重复 Segment 只落库/广播一次。
- `flush()` 产出装配器剩余 Segment。
- `stop(reason)`：flush、结束会话、广播 `transcript.reset`、清空缓冲。
- 持久化或广播失败只计数并记录通用日志，不向上抛异常，避免中断 ASR/采集。
- 运行时会话接入（capture start/stop、generation）留到 P34-05。

### 测试

- 协议：TS 28 passed；Python 39 passed（fixtures 覆盖全部 `EVENT_TYPES`）。
- Handler 新增 9 个：落库+广播、重复、非 final 忽略、stop flush/reset、畸形 payload、
  广播失败仍落库、重复 start、inactive、两条 final 顺序。
- 全量 Python **395 passed / 3 skipped**；ruff 通过。

## 2026-11-22 · P34-05：capture 生命周期与转写会话

基线 `19ce233`；分支 `feature/p3.4-transcript-p3.5-evaluation`。

### 接线

- lifespan 打开数据库后创建 `TranscriptHandler`（Assembler/Buffer/Store/广播）。
- ASR 广播改为 `asr_events`：先广播 `asr.*`，再把 `asr.final` 交给 Handler；
  Handler 异常只记录日志，不影响 ASR 事件与采集。
- `capture.start` 回调：
  1. 生成 `sess_<uuid>`，`handler.start(generation)`；
  2. 再启动 `AsrCoordinator`。
- `capture.stop` 回调：先 `asr.stop()`，再 `handler.stop("capture_stopped")`；
  `asr.stop` 抛错也会在 finally 中结束转写会话。
- 新 generation 启动时 Handler 先按 `generation_changed` 结束旧会话。
- Core shutdown：controller.shutdown → asr.stop → handler.stop("shutdown")，
  Handler 幂等，不重复写结束时间。

### 失败隔离

- 转写落库/广播异常被 Handler 捕获并计数，不向 ASR/采集链路上抛。
- `asr_events` 对 Handler 再包一层异常保护，避免转写故障中断事件广播。

### 测试

新增 4 个应用级测试：capture start 建会话+final 落库、generation 切换结束旧会话、
shutdown 结束活动会话、无会话时 asr.final 忽略。
全量 Python **399 passed / 3 skipped**；ruff 通过。

## 2026-11-22 · P34-06：转写指标与诊断

基线 `25fbdd1`；分支 `feature/p3.4-transcript-p3.5-evaluation`。

### 指标

`TranscriptHandler.snapshot()`（不含正文）：

- 会话：`active`、`sessionId`、`generation`
- 处理：`assembled`、`persisted`、`persistErrors`、`broadcastErrors`
- 装配：`accepted/emitted/duplicates/outOfOrder/merged/gaps/stale/empty/overflowEmitted`
- 缓冲：`appended/duplicates/stale/outOfOrder/gaps/evictedSegments/evictedChars/segmentCount/charCount`

`GET /healthz` 新增 `transcript` 字段；未启动会话时仍返回结构，`active=false`。

### 失败计数

`TranscriptStore.append` 抛 `StorageError` 时 `persistErrors+1`，不广播、不抛；
广播失败 `broadcastErrors+1`，已落库数据保留。

### 测试

新增 3 个：snapshot 计数且不含正文、持久化失败计数、`/healthz.transcript`。

全量 Python **402 passed / 3 skipped**；ruff 通过。

## 2026-11-22 · P34-07：P3.4 集成验收

基线 `890790d`（含 soak transcript 校验）；实际运行 SHA `8fc7644`，`gitDirty=false`。

### 真实链路

`capture.start → WASAPI loopback → VAD → Paraformer → asr.final → TranscriptAssembler
→ TranscriptBuffer → SQLite → transcript.segment → transcript.reset → capture.stop`

执行：`scripts/asr-soak-wasapi.ps1 -Minutes 2 -Interval 20 -KeepData`（TTS 音频）。

| 项 | 结果 |
|---|---|
| Core / Windows | 0.1.0 / 10.0.26300 |
| 模型 | paraformer-realtime-v2 |
| 墙钟 | 122.6s（ratio 1.021） |
| 帧数 | 5,008（短测启动波动，ratio 0.927） |
| ASR Segment / Final | 29 / 29（0 错误） |
| 转写落库 | **34** 条（停止时 ASR flush 额外 5 条） |
| 会话 | 1 个，`status=completed`，`ended_at` 已写 |
| transcript 指标 | 停止前 assembled 26 / persisted 26 / 0 错误 |
| 连接 | 2 次尝试（验证 + 复用），0 失败 |
| 进程内存 | WS 66.2 MB / Private 45.9 MB |

Core 停止后重新打开同一 SQLite：会话仍为 `completed`，34 条 Segment 可查询，
说明不是仅内存状态。

### 修复

- Assembler 默认 `merge_gap_ms=0`，且**时间重叠不合并**：此前重叠/相邻段被吞成
  一条 664 字的长文本；现在保留 VAD 句边界。
- soak 采样 `transcript`，并在停止后只读查询 `transcript_segments` 校验：
  `segments>0` 且最新会话 `completed` 才 PASS；短测帧率阈值 0.85（长测仍 1.0）。

### 验证

Python **402 passed / 3 skipped**；ruff；C# 91；协议 28；桌面 11；typecheck 通过。

## 2026-11-22 · P35-01：ASR 评测数据集

基线 `bb42271`；分支 `feature/p3.4-transcript-p3.5-evaluation`。

### 数据集

`evaluation/dataset/`（11 个样本，约 2.5 MB，16k/16-bit/mono）：

- clean ×2、technical ×1、numbers ×1、mixed ×1、filler ×1、long ×1
- noisy ×3：clean_01 SNR 15/5 dB、technical_01 SNR 10 dB（确定性白噪声）
- silence ×1：3s 无语音控制

每个样本在 `manifest.json` 中有参考文本、类别、时长、采样率、SHA-256 与条件。
语音由 Windows `System.Speech`（Microsoft Huihui）本地合成，**不是真人会议**。

### 隐私

- 仓库只含合成音频；真实会议录音放 `evaluation/local/`（已 gitignore），不提交。
- 本地真实录音需登记授权来源与人工校对 reference；报告只写哈希/时长/指标。
- 不存 API Key、不存会议正文到日志。

### 工具

- `scripts/build-eval-dataset.ps1`：合成 TTS 基线
- `scripts/build_eval_manifest.py`：生成噪声/静音与 manifest（含哈希）
- `evaluation/README.md`：组成、隐私、真实录音接入与评测口径

### 测试

新增 6 个：manifest 结构、文件存在/哈希、音频格式/时长、参考文本、类别覆盖、
仅合成音频边界。

全量 Python **408 passed / 3 skipped**；ruff 通过。

## 2026-11-22 · P35-02：CER/WER 工具

基线 `8f3bd41`；分支 `feature/p3.4-transcript-p3.5-evaluation`。

### 实现

- 新增 `copilot_core/evaluation/metrics.py`：
  - NFKC、小写、去标点/符号/控制字符；
  - CER 以字符计，WER 以 Latin 词 + 单个 CJK 字计；
  - Levenshtein 回填替换/插入/删除计数；
  - 空参考单独统计幻觉，不进入 CER/WER 汇总；
  - 汇总同时给 micro（总错误/总参考）与 macro（逐样本平均）及分类。
- 新增 `scripts/evaluate_asr.py`：
  - 读取 manifest 并校验 SHA-256；
  - 假设来源为 JSON `{sampleId: text}` 或 `--oracle`（仅工具自检，不是 ASR）；
  - 输出 JSON + Markdown，默认不含参考/假设正文，避免会议内容外泄；
  - 缺失假设默认报错（`--allow-missing` 可放宽）。

### 自检

`uv run python scripts/evaluate_asr.py --oracle --label oracle-selfcheck`

结果：11 样本（10 计分 + 1 静音），CER/WER = 0，无幻觉。
报告：`evaluation/reports/cer-wer-oracle-selfcheck.{json,md}`。
该报告是**工具自检**，不是 ASR 识别结果，不得当作 Provider 指标。

### 测试

新增 11 个：归一化、CER/WER 切分、替换/插入/删除、空假设、静音幻觉、
micro/macro、静音排除、分类汇总。

全量 Python **419 passed / 3 skipped**；ruff 通过。

## 2026-11-22 · P35-03：Provider 对比（合成 TTS）

基线 `5429251`；分支 `feature/p3.4-transcript-p3.5-evaluation`。

### 工具

`scripts/eval_providers.py`：

- 对 `evaluation/dataset` 的 11 个样本逐个调用真实 Provider；
- 假设写入 `evaluation/reports/hypotheses-<provider>.json`；
- 调用 `evaluate_asr.py` 生成 `cer-wer-<provider>.{json,md}`；
- 汇总 `provider-comparison.{json,md}`；
- 缺 Key → `NOT_RUN`，连接/样本失败 → `ERROR`/`PARTIAL`，不伪造成 PASS。

### 结果

| Provider | 状态 | 模型 | CER micro | WER micro | 计分样本 |
|---|---|---|---|---|---|
| aliyun | **PASS** | paraformer-realtime-v2 | **12.54%** | 10.53% | 10 |
| deepgram | **NOT_RUN** | - | - | - | - |

Aliyun 分类 CER micro：

| 类别 | CER micro |
|---|---|
| clean | 0.00% |
| filler | 0.00% |
| long | 0.00% |
| noisy（3） | 17.65% |
| mixed | 14.29% |
| technical | 17.95% |
| numbers | 48.00% |
| silence | 0 幻觉 |

### 误差分析

- `numbers_01`：参考“一千二百三十四”“百分之十五”，假设输出 `1234`、`15`；
  语义正确，但当前 CER 未做数字 ITN，导致 48% 的字符差异。
- `technical_01`：`Vue3` → “无三”，`SQLite` → “sq light”；噪声下进一步退化。
- `mixed_01`：`八百` → `800`，同样受数字格式影响。
- 干净句、长句、填充词、15dB 噪声与静音控制均为 0 错误/0 幻觉。

### 限制

- 仅 TTS 合成音频，**不是真实会议**；真实会议 WER 仍 `NOT_MEASURED`。
- Deepgram 无 Key，状态 `NOT_RUN`，不能与 Aliyun 比较质量。
- CER 未做数字/单位 ITN，`numbers` 类别偏高；WER 对中文近似字符级。
- 每类样本量很小（1–3 个），不能外推为总体质量。

## 2026-11-22 · P35-04：ASR Segment 延迟

基线 `2fd3019`；分支 `feature/p3.4-transcript-p3.5-evaluation`。

### 口径

`scripts/eval_latency.py` 对合成集逐样本测量：

- `firstPartialMs`：调用 `transcribe` → 首个 partial；
- `finalMs`：调用 → 首个 final（含整段音频上传）；
- `finalizationMs`：`finalMs - 上传耗时估计`（100ms 音频 / 10ms 间隔 ≈ 10x 实时）。

这是 **Segment 识别延迟**，不是面试回答延迟，也不是真实会议流式字幕延迟。

### 结果

| Provider | 状态 | 有 final | Final P50/P95 | Finalization P50/P95 | 首 partial P50/P95 |
|---|---|---|---|---|---|
| aliyun | **PASS** | 10 / 11 | 1,404.9 / 4,126.2 ms | **771.9 / 2,228.2 ms** | 1,038.0 / 2,995.4 ms |
| deepgram | **NOT_RUN** | - | - | - | - |

- 静音样本无 final，不计入延迟分位；
- 长样本（18.98s）上传耗时估计 1.9s，是 P95 的主要来源；
- 样本量 10，P95 接近最大值，不能外推总体分布。

### 限制

- 音频是离线整段提交并按 10x 实时发送，不是会议中的实时节奏；
- `finalizationMs` 是估算值，依赖固定发送节奏；
- 仅 Aliyun 有结果，Deepgram 无 Key 为 `NOT_RUN`；
- 不代表真实会议/面试回答延迟。

## 2026-11-22 · P35-05：长时间稳定性（30/60 分钟）

分支 `feature/p3.4-transcript-p3.5-evaluation`；TTS 音频，非真实会议。

### 30 分钟单来源

- SHA：`998987297dada9c25283d0c29e59d9491b6c2179`，`gitDirty=false`
- 墙钟 1,803.4s；帧 88,549；VAD 513；final 513；ASR 错误 0
- 转写：DB 521 段，会话 `completed`
- 连接尝试 **2**（验证 + 复用）；PCM 峰值 600,960 B
- final P50/P95：577.7 / 1,314.1 ms
- 内存：WS 65.3 MB / Private 47.7 MB / Peak 68.6 MB

### 60 分钟双来源

- SHA：`efd3e4cccb0bd5117979c1a603f17481e3d4cbb7`，`gitDirty=false`
- 墙钟 3,603.8s；loopback 帧 177,058；VAD 730；final 604（loopback 123 / mic 481）；错误 0
- 转写：DB 608 段，会话 `completed`
- 连接尝试 **3**（验证 + loopback + mic）；PCM 峰值 2,049,920 B
- final P50/P95：284.6 / 3,418.4 ms（P95 由长段拖尾）
- 内存：WS 44.6 MB / Private 52.2 MB / Peak 92.1 MB

报告：`evaluation/reports/stability-30min.json`、`stability-60min-dual.json`。

### 首轮 60 分钟 FAIL 的根因

首轮 60 分钟在 `capture.stop` 后固定等 2s 就查 DB，转写会话尚未标 completed，报告误判 FAIL；
Core 退出后复查为 `completed`、1,214 段。已改为轮询等待 `status=completed`（最多 20s）后重跑，
上表为重跑 PASS。不是转写/ASR 产品故障。

### 限制

- TTS 循环音频，不是真实会议；不代表真实会议稳定性。
- 60 分钟 loopback/mic 的 final 数量差异来自外放拾音/内容，不代表固定比例。
- 单次运行，未做多轮重复；P95 受长段影响明显。
- 内存为单次采样，未做泄漏趋势分析。

## 2026-11-22 · P4：智能问题检测引擎

基线任务书为 `d925b53`（实际 main），P3.4/P3.5 分支未合并；P4 从 main 独立开发，
避免把未审核的 P3.4/P3.5 带入本 PR。

### P4.1 转写管理

`copilot_core/detection/transcript.py`：partial 更新、final 定稿、重复 final 去重、
generation 隔离、loopback/mic 独立、最近 N 条、条目上限、文本 4000 字上限。
Partial 不触发检测。

### P4.2 规则检测

`classifier.py`：问题标记/疑问词/祈使句 + 技术/项目/行为/场景词表；
输出 is_question、类型、confidence、原始/规范化文本、reason、source、segmentId。
confidence 是规则分数，不是校准概率；不接在线 LLM。

### P4.3 追问

`followup.py`：每来源保存主问题；显式追问（那/然后/具体/刚才/它和/这个）关联
最近主问题并给出 parentQuestionId + reason；新主问题替换上下文；超时/无上下文不强行关联。

### P4.4 状态机

`state.py`：COLLECTING/CANDIDATE/CONFIRMED/ANSWERING/ANSWERED 及
IGNORED/CANCELLED/SUPERSEDED/FAILED，非法迁移抛 `InvalidTransition`；
相同规范化文本去重；新主问题令旧候选 SUPERSEDED；支持 cancel_all。
ANSWERING/ANSWERED 只定义状态，不接 LLM。

### P4.5 手动入口

`scripts/detect_question.py`：`--text` 或 stdin，逐条走 `QuestionEngine.ingest_final`
（与真实 asr.final 同一入口）。

### P4.6 评测

`tests/fixtures/detect/labeled.jsonl`：130 条（30 技术 / 20 项目 / 15 行为 /
20 追问 / 45 陈述+闲聊）。
`scripts/detect_eval.py` 结果：**Precision 1.00 / Recall 1.00 / F1 1.00**，
类型准确率 1.00，追问关联 1.00，陈述误报 0%。
这是人工编写规则友好集，**不代表真实会议表现**；样本由同一实现作者编写，
指标偏高，不能外推。

### P4.7 稳定性

空文本、超长文本、非法来源、重复 final、旧 generation、200 条高频输入、
detector 异常隔离（`last_error`，不向 ASR 抛）、cancel_all 均有测试。
检测失败不影响 WASAPI/VAD/ASR：app 的 `asr_events` 捕获异常后继续广播。

### P4.8 集成

`app.py`：ASR coordinator 的广播改为 `asr_events`，仅 `asr.final` 进入检测；
`/healthz.detection` 暴露计数（不含文本）。未新增协议事件，未改 Schema/C#。

### 测试

Python **372 passed / 3 skipped**；ruff 通过；C#/协议/桌面未改。

### 未解决

- 规则表规模小，未做真实会议数据评测。
- 追问只支持显式表达；代词消解/跨主题指代仍可能误关联。
- confidence 未校准，不能作为概率。
- 未接 LLM 复核、未生成 answer 事件、未接 UI。
- 真实会议/真人对话准确率 **NOT_MEASURED**。

## 2026-11-22 · P4 补充：LLM Review / Answer 接口

基线 `eae7e5a`（P4 + P3.4/P3.5 已合并）。

### 范围

本补充只完成**可插拔接口**，不接在线 LLM、不产生真实答案：

- `detection/review.py`：`QuestionReviewer` 协议、`ReviewDecision`、`NoopReviewer`、
  `apply_review()`。可修正 is_question/type/confidence/reason，不能绕过引擎入口。
- `detection/answer.py`：`AnswerProvider` 协议、`AnswerResult`、`UnavailableAnswerProvider`、
  `answer_question()`；负责 CONFIRMED → ANSWERING → ANSWERED/FAILED 迁移，Provider 失败进 FAILED。
- `QuestionEngine(reviewer=...)` 可选注入；默认不注入，规则检测行为不变。

### 状态

- 真实 DeepSeek/任何在线模型：**NOT_RUN / NOT_CONFIGURED**。
- 现有测试全部用 Mock Reviewer / 假 AnswerProvider；没有把 Mock 结果写成真实模型效果。
- 未新增协议事件、未接 UI、未保存答案。

### 测试

新增 6 个：review 覆盖类型/置信度、review 丢弃问题、无决定 no-op、answer 成功迁移、
answer 失败 FAILED、未 CONFIRMED 拒绝。

全量 Python **452 passed / 3 skipped**；ruff 通过。

## 2026-11-22 · P4 补充：DeepSeek 真实 Review / Answer 冒烟

基线 `d43c574`；分支 `feature/p4-deepseek-llm`。

### 实现

- `detection/llm.py`：`DeepSeekClient`（OpenAI 兼容 `/chat/completions`，默认
  `deepseek-flash`）、`DeepSeekReviewer`、`DeepSeekAnswerProvider`、`parse_review`。
- Key 只从 `DEEPSEEK_API_KEY` 读取，不进入仓库/日志/报告。
- 默认规则检测不变；只有显式构造 LLM 类才发请求。
- `scripts/llm_review_check.py`：规则检测 → LLM Review → 确认 → LLM Answer 冒烟。

### 真实 API

```text
规则: technical 0.92
Review: technical 0.99（真实 DeepSeek）
Answer: 652 ms（deepseek:deepseek-flash）
```

单条冒烟通过，不代表分类/回答质量；未接 UI、未接协议、未保存答案。

### 测试

新增 4 个 MockTransport 测试：鉴权/端点、Review 解析、Answer、缺 Key。
Python **456 passed / 3 skipped**；ruff 通过。

## 2026-11-22 · P5：DeepSeek 流式回答

基线 `a2a95f3`；分支 `feature/p5-streaming-answer`。

### 审计

现有 `DeepSeekClient` 只有 `complete()`；`DeepSeekAnswerProvider.answer()` 一次性返回；
无 SSE、无 AnswerContext、无任务取消/generation 隔离。本阶段扩展，不重建客户端。

### 实现

- `DeepSeekClient.stream()`：SSE `data:` 分块、`delta.content`、`[DONE]`、HTTP 4xx/5xx、
  超时/断网、非法 JSON；取消时关闭响应；错误不包含 Key。
- `health_check()` / `model_info()`。
- `AnswerContext` + `build_answer_context()` + `build_answer_messages()`：
  question/parent/recent/resume/selected projects/project_evidence/technical_knowledge/
  answer_style/token_budget；项目证据与通用知识分字段、分段落。
- `DeepSeekAnswerProvider.stream()`：使用统一 Context 构建 messages。
- `AnswerTaskManager`：generation + task_id；新任务取消旧任务；旧 generation 的 delta
  不回调；取消/关闭幂等。
- `scripts/llm_stream_check.py`：真实流式 + 主动取消冒烟。

### 真实 API

`deepseek-flash`：

- 125 chunks / 249 chars，总耗时 1,190 ms；
- 第 1 个 chunk 后取消，任务停止；
- 单条冒烟，不代表回答质量。

### 测试

新增 14 个：SSE 分块/完成、非法 JSON、401/403/429/500、断网、HTTP 错误不带 Key、
流式正常、任务抢占、取消/关闭幂等、Provider 异常、Context 分离、Answer 流式。
Python **470 passed / 3 skipped**；ruff 通过。

### 未解决

- 未接 app 事件/UI；无 answer.delta 协议。
- 未接真实项目证据/知识库（RI 阶段）。
- 未做多轮真实对话评测。

### 变更记录



























| 日期 | 变更 |
|---|---|
| 2026-11-20 | 初始版本：完成 Phase 0 审查与全部设计文档 |
| 2026-11-20 | Phase 1：协议、Core、音频骨架、桌面骨架、CI、安全文档 |
| 2026-11-20 | Phase 2-A：音频二进制协议、Core 接收与统计、C# 发送与模拟流、60 秒端到端 |
| 2026-11-20 | Phase 2-B：WASAPI 设备级采集、重采样分帧、电平统计、真实采集冒烟 |
| 2026-11-20 | Phase 2-C：采集控制面、短期票据、三态冒烟、诊断与 30 分钟稳定性 |
| 2026-11-20 | P2-C.1：控制面并发锁、会话代次、统一清理与迟到状态处理 |
| 2026-11-21 | P2-C.2：锁外 spawn、会话任务绑定、disconnect 竞态与票据边界加固 |
| 2026-11-21 | P3：ASR 协议冻结、票据 purpose、ASR 状态分离与能量 VAD |
| 2026-11-21 | P3.1：完整 PCM Ring Buffer、VAD 运行时流水线与有界 ASR 队列 |
| 2026-11-21 | P3.1.1：PCM 口径、双指标 VAD、Provider 抽象、PCM 预算与生命周期 |
| 2026-11-21 | P3.2：ASR Provider 抽象、Manager、票据校验、事件广播与 Deepgram 适配器（未验证） |
| 2026-11-21 | P3.2.1：Deepgram final 累积、revision、会话级 Manager、generation 隔离与 PCM lease |
| 2026-11-21 | P3.2.2：Deepgram 参数核对、真实 API 自检脚本（本机 SKIP）与文档状态统一 |
| 2026-11-21 | P3.2.3：生命周期竞态、Deepgram READY/Drain/重试、进程内存观测 |
| 2026-11-21 | P3.2.4：阿里云 Paraformer 实时 ASR Provider，真实 API PASS |
| 2026-11-21 | P3.2.5：WASAPI soak 脚本；根因是测试脚本 Minutes 取整，已修正 |
| 2026-11-21 | P3.2.6：30 分钟真实 WASAPI + Paraformer PASS（88,544 帧 / 513 段 / 0 错误） |
| 2026-11-21 | P2-D：按来源统计、Channel 并发、30 分钟模拟/真实稳定性 |
| 2026-11-21 | P2-D.1：设备恢复策略、FrameStreamer 清理、稳定性可信度与 loopback 探测 |
