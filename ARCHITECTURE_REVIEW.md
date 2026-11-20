# 架构审查报告：在 DeepSeek Harness 之上构建 RealTime Copilot

审查对象：DeepSeek Harness 源码检出（本机路径已隐去；git `master`，`687f282e86`，版本 `0.2.1-rc.2`）
审查时间：2026-11-20
审查范围：只读。未修改、未删除、未覆盖该仓库的任何文件；本报告与全部设计文档写在工作区的独立目录 `default-workspace/realtime-copilot/`。
代码状态：`git status` 只有一处既有未跟踪目录 `default-workspace/`，本次审查未新增任何改动。

---

## 0. 结论先行

1. **Harness 是一个 Cordis 全插件 Node/TypeScript 应用**（Node `^22.19 || >=24`、pnpm 11、ESM、tsc + tsdown、vitest），不是"提供某些 API 让我接入的框架"。Integration 的唯一受支持单位是 **profile / bundle / plugin 组合**，其次才是进程外的 **SDK JSON-RPC** 与 **ACP**。它没有 HTTP 业务 API，Web 端的 `/api` 是内部 Typert RPC，需要 Peer 与 remote-access 授权，**不能当作对外集成面**。
2. **Harness 的 Web 客户端是 React 18 + 自有 Slot 体系，仓库内不存在 Vue**。因此"Electron + Vue3"只能用于我们自己的独立悬浮窗应用，不可能作为 Harness 的 client plugin 嵌进它的界面。这是硬约束，不是偏好问题。
3. **Harness 没有"知识库接口"。** 全仓库没有 knowledge/knowledge-base 服务；与知识最接近的现成接缝是 `dsh-mcp-resources`（模型按需读取 MCP 资源）、skill 加载、以及 `agent/pre-step` 注入的运行时上下文。知识库必须由我们自建，并以 MCP server 或 Context Provider 的形式暴露。
4. **模型接缝是 `ctx.llm`**：`LlmAdapter` + `registerAdapter` + `StreamChunk` 协议 + `GenerateOptions`，DeepSeek 通过 `dsh-llm-deepseek-api-key` 注册 `deepseek-official` 路由。这条接缝可以复用，但它是给 agent loop 用的；直接调用需要 `LlmRuntime.stream()`/`prepareCall()` 这类面向 loop 的 API，属于"可验证但非宣传用途"的用法（见未知问题 U6）。
5. **权限系统的两个旋钮是 sandbox mode 与 approval policy**，由 `dsh-permission-presets` 打包成 preset。默认 `read-only`；本会话被设为 `danger-full-access` + 不询问，这是**本机操作者**的选择，不能成为产品默认，更不能成为"自动操作会议软件"的授权。
6. **音频侧没有任何可复用实现**。Harness 只处理麦克风权限（`apps/desktop/src/microphone-permissions.ts`，且是为其自身渲染进程的 WebRTC 服务），没有 WASAPI、没有 ASR、没有 VAD、没有会话音频。这一整块是全新工程。
7. 延迟目标（P50 < 1s / P95 < 2s，从"最后一个有效音频片段"起算）**当前既未实现也未测量**。按公开资料与该协议的常识拆解，它在"流式 ASR + 短提示词 + 流式 DeepSeek"下是**紧但可能**的；必须实测，且必须允许在达不到时下调承诺而不是伪造数据。

---

## 1. 审查方法与证据边界

**读过的内容**（均为仓库内真实文件）：

- `README.md`、`AGENTS.md`、`package.json`、`pnpm-workspace.yaml`、`CONTRIBUTING.md`、`SAFETY.md`
- `docs/architecture.md`（全文）、`docs/testing.md`（全文）、`docs/cookbook/adding-a-package.md`（全文）
- `docs/subsystems/llm-streaming.md`（前 860 行，覆盖 `StreamChunk`、`LlmFailure`、`GenerateOptions`、adapter 契约、`PreparedLlmCall`）
- `packages/bundle/base/{package.json,cordis.patch.yml}`、`packages/bundle/web-app/package.json`
- `packages/context/time-context/src/index.ts`（完整插件样例）
- `packages/client/AGENTS.md`（前 140 行）、`packages/client/ui-goal/package.json`
- `packages/interaction/permission-presets/README.md`、`packages/sandbox/sandbox-policy/README.md`
- `packages/llm/llm-deepseek-api-key/README.md`、`packages/sdk/server/README.md`、`packages/api/gateway/README.md`
- `packages/mcp/mcp-resources/README.md`、`packages/boot/plugin-manager/README.md`
- `apps/desktop/package.json`、`apps/desktop/README.md`（按关键词检索 Windows/签名/麦克风相关段落）
- `apps/desktop/src/microphone-permissions.ts`（grep 确认）
- `packages/credentials/credentials-local/src/index.ts`（grep 权限位相关实现）
- `python/sdk/pyproject.toml`

**没有做的事**（不得据本报告推断）：

- 没有运行 `pnpm install` / `pnpm run build` / 任何测试。仓库体积大（342 个 workspace 包），且本阶段不需要。
- 没有启动 `dsh`，没有验证任何插件在运行时的真实加载行为。
- 没有审计全部 342 个包；结论基于架构文档 + 关键包的 README 与源码。
- 没有验证 Windows 上的构建可行性（CI 用 wine 做 Windows 信号，本地未复现）。

因此下文把结论分成两类：**已核实（附路径）** 与 **待验证（U 编号）**。设计文档只依赖前者，后者进入实验清单。

---

## 2. 已核实的技术栈事实

| 事实 | 证据 |
|---|---|
| 单一 pnpm workspace，342 个包，分组目录 `packages/<group>/<pkg>` | `pnpm-workspace.yaml`、`packages/` 目录 |
| Node `^22.19.0 || >=24.0.0`，pnpm `11.28.5`，全 ESM（`"type": "module"`） | 根 `package.json` |
| TypeScript `^6.0.3`，双构建面 `tsconfig.host.json` / `tsconfig.client.json`，打包用 tsdown，产物 `lib/` | 根 `package.json` scripts、`docs/development.md` |
| 测试用 vitest 4，分层：unit / coverage / e2e / expected / snapshot / web / bench | `docs/testing.md` |
| 桌面端 Electron `^44.7.0` + electron-builder 26，已有 `package:win:x64` 全链路 | `apps/desktop/package.json` |
| Web 客户端 React 18 + 自有 Slot 注册体系 | `packages/client/ui-goal/package.json`（`react ^18.2.0`）、`packages/client/AGENTS.md` |
| 插件 = Cordis 模块：导出 `name` / `inject` / `apply(ctx, config)` / `Config`(schemastery) | `packages/context/time-context/src/index.ts` |
| 组合 = profile → bundles（patch 文件）→ 用户 `cordis.patch.yml` → `--patch` 覆盖；patch 按 `id` 整行替换 config 或 `insert` 新行 | `docs/architecture.md`、`packages/bundle/base/cordis.patch.yml` |
| bundle 用 `package.json` 的 `dsh.bundle.patch` 声明 patch 路径 | `packages/bundle/base/package.json` |
| client plugin 用 `dsh.client.inject` + `exports["./client"]` | `packages/client/ui-goal/package.json` |
| 模型接缝：`ctx.llm`，`registerAdapter(providers, adapter)`，`StreamChunk` 封闭联合，`usage` 必须在 `finish` 前、`finish` 后无内容 | `docs/subsystems/llm-streaming.md` |
| DeepSeek 适配器 + 官方 key 路由 `deepseek-official`，`apiKeyEnv` 默认 `DEEPSEEK_API_KEY` | `packages/llm/llm-deepseek-api-key/README.md` |
| 凭证落在文件（POSIX `0600`/`0700`），**Windows 上没有 POSIX mode 可校验** | `packages/credentials/credentials-local/src/index.ts` 注释与 `assertOwnerOnly` |
| 权限 = sandbox mode（`read-only` / `workspace-write` / `danger-full-access`）+ approval policy（`ask` / `never`），默认 `read-only` | `packages/sandbox/sandbox-policy/README.md`、`packages/interaction/permission-presets/README.md` |
| 进程外集成面：SDK stdio JSON-RPC（`initialize` / `session/prompt` / `session/wait` / `shutdown`），无 per-prompt 结果、无 per-session close | `packages/sdk/server/README.md` |
| Web `/api` 是 Typert RPC，需要 Peer 身份与 `remoteAccess` 决策，不是公开 API | `packages/api/gateway/README.md` |
| Python SDK：`requires-python >=3.10`，pydantic v2，uv + pytest | `python/sdk/pyproject.toml` |
| 公开 API 处于 pre-stable，`0.2.1-rc.2` developer preview，明确会有破坏性变更 | `README.md`、`AGENTS.md` |

---

## 3. 插件系统：真实契约

### 3.1 一个插件由什么构成

Cordis 模块的最小形态（以 `time-context` 为模板）：

```ts
export const name = 'time-context'                       // loader 诊断名
export const inject = ['agents', 'sessionProjections']   // 服务依赖，缺失时 fiber 挂起
export interface Config { /* ... */ }
export const Config: z<Config> = z.object({ /* ... */ })  // schemastery 运行时校验
export function apply(ctx: Context, config: Config): void { /* 注册全是 effect */ }
```

约束（`AGENTS.md` + 实测样例）：

- **注册即 effect**：`ctx.on` / `ctx.effect` / `ctx.tools.register(...)` 返回 disposer，插件卸载时自动回收。禁止模块级单例副作用。
- **配置不是常量**：会随部署变化的量必须是 `Config` 字段并在 `cordis.patch.yml` 里可改；协议常量与安全不变量才允许硬编码。
- **失败要响**：配置错误在加载期抛错，不静默降级。
- **包清单不变量**：`private: true`、版本与根一致、`type: module`、`main: lib/index.js`、`types: lib/types/index.d.ts`、`@deepseek-ai/cordis` 同时出现在 `peerDependencies` 与 `devDependencies`（`docs/cookbook/adding-a-package.md`）。

### 3.2 组合与安装

- **profile**：命名组合，存于 Harness home，列出它叠加的 bundles、安装的树外插件、用户 `cordis.patch.yml`。出厂 profile：`web`、`headless`、`sdk`、`sdk-minimal`、`acp`。
- **bundle**：可发布的分发单位，`package.json` 里 `dsh.bundle.patch` 指向 patch 文件；`dsh-base` 是所有 base 型 profile 的第一层。
- **patch 语义**：按 `id` 定位整行，**整块替换 config**（不合并），或 `insert` 新行。行序不携带加载语义，激活顺序由服务可用性决定。
- **安装第三方插件**：`dsh plugin` 管理命令 / Plugin Manager（`packages/boot/plugin-manager`）用 pnpm 装进 profile 的 `dependencies`，写进 `dsh.profile.bundles`。

对我们的含义：**Harness 模式应发布成一个 bundle 包**（内部一行 insert 指向 bridge 插件），这样它可被 `dsh plugin` 安装、可被用户 patch 覆盖、也可被单独禁用——而不是要求用户手改 YAML 深处。

### 3.3 事件与扩展点

- **Session events**：持久化事实，追加进 log 并通过 `session/event` 广播。模型可见 ⟺ 已记录。
- **Agent events**（`agent/*`）：携带活 Agent，用于观察/拦截进行中的工作。`agent/pre-step`、`agent/request`、`llm/stream`、`tools/*` 是 **waterfall**，监听器**必须调用 `next()`** 才能委派。
- **Capability events**（`fs/*`、`tools/*`、`telemetry/*`）：给接缝挂策略与适配器。

`docs/architecture.md` 的 "Where new behavior goes" 表给出了每种新行为的唯一正确挂载点。与本项目相关的四行：

| 我们要做的 | Harness 规定的机制 |
|---|---|
| 给模型看会议上下文 | `agent.inject()` / `agent/pre-step` 返回带 `source` 的 user message |
| 让 agent 主动查会议内容 | 注册 `ctx.tools` 工具 |
| 加一个用户命令 | 注册 `ctx.commands` |
| 加 UI | 注册 Conversation Node 或 Slot（React） |

---

## 4. 模型接口：能复用什么、不能假设什么

### 4.1 能复用的

- `ctx.llm` 的 provider 注册与路由选择：用户已在 Harness 里配好的 DeepSeek key、baseURL、模型、reasoning effort、重试策略，可直接复用，无需我们二次存储密钥。
- `StreamChunk` 协议（`block-start` / `text-delta` / `block-end` / `usage` / `finish`）与 `BlockAssembler`：如果 H2 模式要复用，装配逻辑是现成的。
- `deepseek-official` 路由的凭证解析顺序（credentials 服务优先，缺服务才读环境变量）。
- 重试与失败归一：`LlmFailure { message, code, status?, providerRetryAfterMs?, requestId? }`，`EMPTY_RESPONSE`、`CONTEXT_WINDOW_EXCEEDED` 等稳定 code。

### 4.2 不能假设的

- **不能假设可以直接 `fetch` DeepSeek**：Harness 的 adapter 有自己的 header（`attributionHeaders()` 的 User-Agent 基线）、endpoint 解析、扩展字段事务（`ctx.deepseekLlmApiExtensions`）。绕过它会产生与产品不一致的请求。若走 H2，就应走 `ctx.llm`；若走独立模式，就明确这是我们自己的客户端，与 Harness 的请求语义无关。
- **不能假设 `ctx.llm.stream()` 是公开稳定 API**：文档明确"direct `ctx.llm.stream()` callers remain single-attempt"，它存在且可调用，但面向的是辅助调用者（标题、压缩），不是第三方产品主路径。U6 要在 Phase 7 实测。
- **不能假设 retry 由适配器做**：`one adapter call is one provider attempt`，重试是 `dsh-llm-retry` 在 agent 层做的。H2 若直接调 adapter，就没有这层重试，需要自己实现或在 bridge 里用 `llmRetryPolicyOf`。

### 4.3 延迟相关的事实

- 适配器有 `streamIdleTimeoutMs`（默认 5 分钟），对首 token 延迟没有约束——它是防挂死，不是延迟预算。
- `usage` 必须在 `finish` 前到达：这意味着**不能靠 usage chunk 判断首 token**，必须以第一个 `text-delta` 为 T1。
- `EMPTY_RESPONSE` 会被当作可重试错误——对低延迟场景，重试等于延迟翻倍，必须在上层做"宁可显示空态也不重试"的选择（或者只对幂等、非首答场景重试）。

---

## 5. 知识库：Harness 没有这个接口

**核实结论：全仓库没有 knowledge base 服务。** 检索 `knowledge|Knowledge` 只命中 `acknowledge` 一类词与文案，没有任何 `ctx.knowledge` / `KnowledgeService` / 向量检索包。

现成的三类替代接缝：

1. **MCP 资源**（`packages/mcp/mcp-resources`）：配置 MCP client 后，模型拿到 `list_mcp_resources` / `list_mcp_resource_templates` / `read_mcp_resource` 三个工具，**按需**读取文档，文本进入对话历史。这是 Harness 官方认可的外部知识入口，也是我们 KB 最省事的暴露方式：**把 KB 做成一个 MCP server**，Harness 侧零改动即可消费。
2. **Context 注入**（`packages/context/*`、`agent/pre-step`）：`time-context` 的写法就是模板——在 pre-step 里返回一条 `source: { kind, form: 'snapshot', sections }` 的 user message，模型可见且可重建。
3. **Skill / 指令文件**：适合"操作手册"型知识，不适合动态检索。

对我们的含义：KB 自建在 Copilot Core 内（SQLite + 可选 embedding），对外提供两种读法——(a) 我们自己的 `KnowledgeProvider` 给答案生成用；(b) 一个 MCP server 给 Harness agent 用。两者共用同一份存储与检索实现，避免两套语料。

---

## 6. 权限与安全：现状与差距

### 6.1 已核实的机制

- **sandbox mode**：`read-only`（默认，fail-safe）/ `workspace-write` / `danger-full-access`，按 session 记录为 `sandbox/mode` 事件，重启后由 replay 恢复。
- **approval policy**：`ask` / `never`，与 sandbox 由 `dsh-permission-presets` 打包成 preset；`custom` 是派生状态不可选。
- **远程访问**：Gateway 的每次调用都要带 Peer，非 owner 调用要经 `remoteAccess.decide()`。
- **桌面麦克风**：仅允许主 `dsh-app://app` 帧请求，macOS 还要系统授权。

### 6.2 差距（对我们的产品是硬要求）

| 要求 | Harness 现状 | 我们的处理 |
|---|---|---|
| 录音必须用户主动开启、状态可见 | 无会议音频概念 | 我们自己的显式开关 + 悬浮窗常驻指示 + 托盘状态；默认关闭，启动不自动开 |
| 默认不持久化原始录音 | 无音频存储 | Core 默认只在内存环形缓冲；落盘需显式开启且单独告知 |
| API Key 安全存储 | 文件 + POSIX `0600`；**Windows 无 mode 校验**，仅依赖 ACL | 独立模式用 Electron `safeStorage`（Windows 走 DPAPI）；Harness 模式可选择复用 Harness 凭证（并接受其风险）或用自己的存储 |
| 本地服务仅监听 127.0.0.1 | 不适用 | 所有本地监听显式绑定 `127.0.0.1`，绝不 `0.0.0.0`；启动时断言 |
| 未经授权不向会议自动发消息 | 不适用 | v1 **完全不实现**输入注入/自动发送；没有该代码路径，就没有误用路径 |

注意：本会话的操作者把 approval policy 设为"从不询问"，这**不代表**产品可以对会议软件做任何自动操作。产品侧的安全边界必须写在代码与配置里（无注入模块、无 UI 自动化依赖），而不是依赖 approval。

---

## 7. 构建、测试、打包：可继承的工程实践

- **构建**：`build:artifacts`（tsc 出 `lib/types` + tsdown 出运行时 + Web）与 `build:check`（对当前产物做类型检查）分离。`test` 不等于 `test:coverage`，后者才是 CI 门禁。
- **测试分层**（`docs/testing.md`）：unit / coverage（每文件 100%）/ real-API e2e（无 key 自跳过）/ expected / snapshot / web / bench。对我们的直接启发：**我们的性能门禁应做成 `benchmarks/` 式的独立 lane**，而不是混在单测里。
- **打包**：`apps/desktop` 已有完整的 `package:win:x64`、签名、安装器测试（`scripts/test-windows-installer.mjs`）、`render:tray-icon`、以及独立的 [windows-signing](../../apps/desktop/windows-signing/README.md) 指南。**这是我们该抄的作业**：Electron shell + 紧凑打包 + 签名 + 安装器验证。
- **Python 运行时**：Desktop 已经把构建好的解释器与 wheels 打进 `resources/runtime`（并按 target 生成 lock.json）。如果我们的 Core 用 Python，这是可参考的先例，但**复用它等于依赖 Harness 的发布流水线**，我们应自建同构但独立的打包。

---

## 8. 可复用 / 需新增 / 不能复用

### 8.1 可直接复用（作为依赖或作为模式）

| 对象 | 复用方式 | 注意 |
|---|---|---|
| `ctx.llm` + `dsh-llm-deepseek-*` | H2 模式下由 bridge 代理模型调用 | 版本绑定 `0.2.1-rc.2`，pre-stable |
| `ctx.tools` / `defineTool` | bridge 注册 `copilot_*` 工具 | 需 peerDepend `@deepseek-ai/dsh-tools` |
| `agent/pre-step` + `createUserMessage` | 把会议上下文注入 Harness 会话 | 会**进入持久会话日志**，需用户显式开启 |
| `dsh-mcp-client` / `dsh-mcp-resources` | KB 以 MCP server 暴露 | 我们只写 server，不碰 Harness |
| `dsh-settings` | bridge 的配置入口 | 需要读它的 README 再定字段模型（U9） |
| `apps/desktop` 的打包/签名/托盘/麦克风权限实现 | **模式复用**，不是代码复用 | 我们的 shell 是 Vue3，不能直接搬 React |
| `packages/credentials` 的接缝 | 若 H2 复用 Harness 凭证 | Windows 上无 mode 校验 |
| `docs/testing.md` 的分层与 `benchmarks/` 形态 | 测试策略模板 | — |

### 8.2 必须新增

1. **C# WASAPI 采集 sidecar**：进程级/设备级 loopback、可选麦克风、重采样到 16k mono、分帧、设备热插拔、权限与错误上报。
2. **Copilot Core（Python FastAPI）**：会话状态机、VAD、ASR 编排、转写装配、问题检测、答案生成、事件总线、SQLite。
3. **Provider 层**：`AsrProvider`、`VadProvider`、`QuestionDetector`、`LlmProvider`、`KnowledgeProvider`（接口见 `PLUGIN_API.md`）。
4. **事件协议与代码生成**：一份 JSON Schema → TS 类型 + Python pydantic 模型，双端同源。
5. **Electron + Vue3 悬浮窗**：置顶、透明、点击穿透、内容保护（防屏幕共享泄漏）、状态指示、设置页、托盘。
6. **Harness bridge 插件**：TS Cordis bundle，连本地 Core，注册工具，按需注入上下文。
7. **打包与安装**：三进程编排（sidecar + core + UI）、签名、升级、首次运行引导。

### 8.3 明确不复用

- **Harness 的 Web `/api` Gateway**：内部 RPC，需要 Peer 与 remote-access 授权，语义与版本都不对外承诺。依赖它等于把产品绑死在一个私有协议上。
- **Harness 的 Session/Conversation UI**：React Slot 体系，Vue3 无法接入；且会议悬浮窗形态与聊天界面完全不同。
- **Harness 的 Electron main**：它有自己的 profile 所有权、更新单元、签名链；我们的应用是独立产品，不应混入。

---

## 9. 兼容性风险登记

严重度：P0 阻断 / P1 高 / P2 中 / P3 低。

| ID | 风险 | 严重度 | 依据 | 缓解 |
|---|---|---|---|---|
| R1 | Harness 处于 developer preview，公开 API pre-stable，明确会有破坏性变更 | P1 | `README.md`、`AGENTS.md` | bridge 只依赖极少数包（`cordis`、`dsh-tools`、`dsh-agent`）；锁定精确版本；维护兼容矩阵（`TEST_PLAN.md` §7） |
| R2 | Web 客户端是 React 18，不存在 Vue3 | P0（对"Harness 内嵌 Vue 面板"这一设想） | `packages/client/ui-goal/package.json`、`packages/client/AGENTS.md` | Vue3 只用于我们自己的 Electron 窗口；Harness 内最多提供极简 React 面板或干脆不提供 |
| R3 | 第三方插件依赖解析：`@deepseek-ai/dsh-*` 需以 peer 形式与宿主同版本，pnpm 安装树外 bundle 时可能装出第二份单例 | P1 | `AGENTS.md`（`@deepseek-ai/cordis` peer 规则）、`plugin-manager` 用 pnpm 安装 | 全 peer + 精确版本；在干净 profile 里用 `dsh plugin` 实测安装（U9）；必要时把 bridge 做成"零 dsh 运行时依赖"（只用 `ctx` 结构类型 + 动态取工具服务） |
| R4 | Windows 上凭证文件无 POSIX mode 校验，仅靠 ACL | P1（安全） | `credentials-local/src/index.ts` 注释 | 独立模式用 `safeStorage`/DPAPI；Harness 模式明确告知风险，或改用我们自己的加密存储 |
| R5 | 进程级 loopback（`AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK`）微软文档最低要求 Windows 10 build 20348；Win10 19044/19045 属"实践可用但无承诺" | P1 | Microsoft 文档 + 社区实现 | 设备级 loopback 作为必备回退；启动自检 + 明确告知"当前为整机输出采集" |
| R6 | 会议软件可能使用独占/受保护音频路径，导致 loopback 静音 | P1 | 无法从文档确认，需实测 | Phase 2 用腾讯会议/微信实测并记录；提供"检测到静音"提示与切换设备 |
| R7 | 延迟目标可能在真实网络下达不到 | P1 | 未测量 | 先测后承诺；按阶段公开 P50/P95；不达标时降级为"P95 < 2s 需更短提示词/更快 ASR" |
| R8 | Electron 悬浮窗可能被录屏/共享捕获，泄漏给会议对方 | P1（隐私） | Electron `setContentProtection` 在 Win10 2004+ 可用但有边界 | 默认开启内容保护；在 UI 明示；TEST_PLAN 有专项用例 |
| R9 | Python 运行时打包体积、SmartScreen、杀软误报 | P2 | Desktop 已有先例但也承认签名/策略问题 | 复用其打包经验；优先 onedir 而非 onefile；预留签名预算 |
| R10 | 麦克风与扬声器同时采集会产生回声/重复转写 | P2 | 物理事实 | 默认只采 loopback；麦克风需显式开启；后续可选 AEC |
| R11 | Harness 会话日志是持久事实，"注入会议上下文"会写进用户会话并可能被上传（`session-log-deepseek`） | P1（隐私） | `docs/architecture.md`、"Model-visible ⟺ logged" | 注入默认关闭；开启时明确告知会写入会话日志；提供"仅本地不注入"模式 |
| R12 | 在本仓构建/测试需要 Node 22.19+、大量磁盘、Windows 上 native addon 与 wine 门禁 | P2 | 根 `package.json`、`AGENTS.md` | 我们不修改 Harness 源码，只在独立目录开发；bridge 单独一个小仓库/包，不阻塞于 Harness 全量构建 |

---

## 10. 未知问题（必须用实验回答，不能靠推断）

| ID | 问题 | 影响 | 验证方式 | 归属阶段 |
|---|---|---|---|---|
| U1 | DeepSeek 在"短提示词 + 200 token 上限"下的首 token 延迟分布（本机、本网络） | 直接决定 P50/P95 能不能达标 | 100 次真实调用，记录 TTFB 分布 | Phase 5 |
| U2 | 候选流式 ASR 的中文会议场景 WER 与"最后片段定稿延迟" | 决定 ASR 选型与锚点定义 | 用录制样本跑评测脚本 | Phase 3 |
| U3 | Win10 19044/19045 上进程级 loopback 是否稳定可用；腾讯会议/微信是否可被单独捕获 | 决定"只采会议音频"能否成立 | Phase 2 实机矩阵 | Phase 2 |
| U4 | 会议软件是否走受保护音频路径导致 loopback 无声 | 产品可用性 | 同上，含"共享屏幕 + 播放语音"场景 | Phase 2 |
| U5 | 真实链路的端到端延迟预算拆解（采集→VAD→ASR→检测→LLM→渲染） | 优化方向 | 埋点 + 直方图 | Phase 5/9 |
| U6 | 第三方插件能否稳定调用 `ctx.llm.stream()` / `prepareCall()`，以及版本绑定代价 | H2 可行性 | 在干净 profile 写最小插件实测 | Phase 7 |
| U7 | `setContentProtection` 在 Win10/11、不同共享方式下的实际遮挡效果 | 隐私承诺 | 用腾讯会议/系统截图/浏览器共享三路验证 | Phase 6 |
| U8 | 外放时麦克风回声对转写的污染程度 | 是否必须做 AEC | 录制对比实验 | Phase 3/9 |
| U9 | `dsh plugin` 安装树外 bundle 的真实体验与依赖解析结果 | 交付方式 | 干净 Harness home 实测 | Phase 7 |
| U10 | 流式 ASR 的 partial 结果能否用于"提前触发"以把首 token 压到 1s 内 | 可能是达标关键 | 用 partial 触发 + 最终结果复核的策略做 A/B | Phase 9 |

---

## 11. 对用户原始技术选型的修正建议

| 原设想 | 审查结论 | 建议 |
|---|---|---|
| Electron + Vue3 桌面界面 | 与 Harness 无关的部分成立；但**不能**作为 Harness client plugin | 保留 Vue3，仅用于我们自己的悬浮窗/设置窗口；Harness 内不嵌 Vue |
| C# + NAudio + WASAPI | 成立，且是 Windows 上最稳的路径 | 保留；增加设备级回退与静音检测 |
| Python FastAPI 业务编排 | 成立，但要注意：Harness 插件本身必须是 TS，Python 只能作为**进程外**服务 | 保留 Python Core；bridge 用 TS 做薄代理；明确 Core 是唯一编排实现 |
| WebSocket 实时通信 | 成立 | 保留；音频走 sidecar→core 的二进制通道，UI 只收事件 |
| 可插拔 ASR Provider | 成立 | 接口先定，供应商后选（Phase 3 评测） |
| DeepSeek SSE 流式 | 成立 | 独立模式直连；Harness 模式可选走 `ctx.llm`（H2） |
| SQLite 本地数据 | 成立 | 保留；Harness 已有 `session-query-sqlite` 先例，但我们的库独立 |

**一个额外的架构建议**：把"音频采集"与"业务编排"分成两个进程，不只是为了语言选择，而是为了**故障隔离**——WASAPI 驱动异常、设备热插拔崩溃不应带走 UI 与已生成的答案。同理 Core 崩溃不应让悬浮窗消失。三进程（UI / Core / Audio）用 supervisor 编排，任一崩溃可单独重启并回放最近 N 秒状态。

---

## 12. 审查产出与下一步

- 目标架构与双模式设计：见 [ARCHITECTURE.md](ARCHITECTURE.md)
- 接口、协议、配置：见 [PLUGIN_API.md](PLUGIN_API.md)
- 十阶段计划与验收：见 [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)
- 测试与性能测量：见 [TEST_PLAN.md](TEST_PLAN.md)
- 本轮动作流水：见 [BUILD_LOG.md](BUILD_LOG.md)

下一阶段（Phase 1）只做工程骨架与协议定义，**不写音频、不接模型**，目的是让 Phase 2 的音频实验有一个可测量、可回放的宿主。
