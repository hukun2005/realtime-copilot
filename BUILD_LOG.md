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
| Electron smoke（`electron . --smoke`） | exit 0，输出 `{"smoke":"connected","status":{"phase":"connected","serverVersion":"0.1.0","protocol":1,...}}` |

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

### 尚未解决 / 下一阶段前置

- 音频采集完全未实现（Phase 2）：WASAPI 设备级与进程级 loopback、重采样、分帧、二进制帧通道。
- Core 尚未处理 `role=audio` 的音频帧（当前回 `not_implemented`）。
- 桌面端还没有悬浮窗形态、托盘与设置页（Phase 6）。
- `safeStorage` 与 API Key 流程未接入（Phase 5）。
- CI 首次运行结果需在本轮推送后确认；Windows 作业依赖 GitHub 托管 runner 的 .NET 10 支持。

### 下一步

Phase 2：实现 `IAudioFrameSource` 的 WASAPI 设备级采集（先不做进程级），把 20ms PCM16 帧通过二进制 WebSocket 帧发给 Core，并在 Core 侧统计帧率与丢帧；随后按 U3/U4 在两个 Windows 版本上实测腾讯会议与微信。

### 变更记录

| 日期 | 变更 |
|---|---|
| 2026-11-20 | 初始版本：完成 Phase 0 审查与全部设计文档 |
| 2026-11-20 | Phase 1：协议、Core、音频骨架、桌面骨架、CI、安全文档 |
