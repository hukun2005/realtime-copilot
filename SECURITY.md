# 安全模型（Phase 1）

本文说明当前代码**实际**提供的保护，以及它不提供什么。每条都对应代码位置，不写"计划中"的能力。

## 1. 信任边界

```text
用户会话
 ├─ Electron 主进程（Node）        —— 读 runtime.json，连 Core
 ├─ Core（Python, 127.0.0.1）      —— 持有令牌、SQLite、事件总线
 ├─ audio-capture（C#）            —— 读 runtime.json，连 Core
 └─ 其他同用户进程                  —— 能读同一批文件、能连回环端口（见 §7）
```

边界外的东西（其他 Windows 用户、网络对端）无法访问：Core 只绑定 `127.0.0.1`，没有对外监听。

## 2. 监听地址

- 配置模型把 `server.host` 固定为 `Literal["127.0.0.1"]`（`services/copilot-core/src/copilot_core/config.py`），`0.0.0.0` / `::` / 局域网地址在解析期直接抛 `ConfigError`，进程以退出码 2 结束。
- 连接层再做一次纵深防御：`EventConnection._is_loopback()` 只接受 `127.0.0.1` / `::1` 来源，其他来源立即关闭（`ws/connection.py`）。
- 实测：`Get-NetTCPConnection -LocalPort <port> -State Listen` 显示 `LocalAddress=127.0.0.1`。

## 3. 令牌

| 项目 | 实现 |
|---|---|
| 生成 | `secrets.token_hex(32)`，256 位随机，每次 Core 启动重新生成（`security.generate_token`） |
| 传递 | 写入 `runtime.json`，由同机的 UI / 音频进程读取；不经过命令行参数、不经过环境变量 |
| 比较 | `hmac.compare_digest` 常量时间（`security.tokens_equal`） |
| 生命周期 | 进程退出即失效；重启后旧令牌不可用 |
| 传输 | 只在 `hello` 首帧出现一次；服务端不回显、不写日志 |

鉴权失败的行为：回一条 `system.error{code:"auth_failed"}` 后关闭连接；2 秒内未发送 `hello` 回 `auth_timeout` 并关闭（`server.auth_timeout_ms`）。

**所有角色共用同一令牌**：`ui` / `bridge` / `audio` 只在 `hello.payload.role` 上区分，服务端按角色限制可用消息类型（§7），但不校验进程身份。短期音频凭证的设计与未实现原因见 §7。

## 4. 文件保护：Windows ACL 与 DPAPI 的真实取舍

`runtime.json` 位于数据目录（Windows 默认 `%LOCALAPPDATA%\RealTimeCopilot`）。

- **POSIX**：`chmod 0600` 是强制语义，`restrict_to_owner()` 返回 `True`。
- **Windows**：`os.chmod` 只影响只读位，**不构成保护**。因此 `security._restrict_windows()` 调用 `icacls`：
  1. `/inheritance:r` 去掉从父目录继承的 ACE；
  2. `/grant:r <当前用户>:F` 只授予当前用户。
  失败时返回 `False` 并记录 `hardened:false` 日志，此时退化为"依赖 `%LOCALAPPDATA%` 的用户级目录 ACL"。

**为什么不用 DPAPI 加密 runtime.json**：DPAPI 的密文绑定加密时的用户（及可选的进程/会话）上下文，且 Electron 的 `safeStorage` 使用自己的格式前缀，Core 用 `CryptProtectData` 生成的 blob 无法被 `safeStorage.decryptString` 解开。若强行加密，三个进程将无法共享同一个令牌——除非再引入一个共享的密钥服务，反而扩大攻击面。因此这里的选择是：

> 令牌视为**本地能力凭证**（证明"你是本用户启动的这个 Core"），而不是长期机密；
> 保护手段 = 用户级目录 + ACL + 每次启动轮换 + 只绑定回环 + 短鉴权窗口。

**API Key 不进入 Core。** Phase 5 起由 Electron 的 `safeStorage`（Windows 走 DPAPI）持有，并通过已鉴权的通道下发短期会话凭证；Core 不落盘任何模型密钥。

## 5. 日志

`logging_setup.JsonFormatter`：

- 敏感键名（`token` / `api_key` / `authorization` / `password` / `secret` / `credential` …）的值一律替换为 `[redacted]`，即使调用方误传。
- 单字段截断到 2048 字符，避免把转写正文写进日志。
- 只输出 JSON 标量与异常摘要，不输出请求体。

测试覆盖见 `services/copilot-core/tests/test_logging.py`。

## 6. Electron IPC

- `contextIsolation: true`、`nodeIntegration: false`、`sandbox: true`。
- preload 只暴露四个方法：`snapshot` / `audio` / `reconnect` / `onCoreStatus`（`src/shared/bridge.ts`），**没有通用 `send` 或 `invoke`**。
- 主进程只注册 `copilot:snapshot`、`copilot:audio`、`copilot:reconnect` 三个 handler 与一个 `copilot:core-status` 推送通道。
- 页面通过自定义协议 `copilot://app/` 加载（`protocol.handle`，越界路径返回 403），并携带 CSP：`default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'`。使用 `file://` 时 `'self'` 无法匹配打包后的模块脚本，因此没有采用它。

## 7. 音频通道权限与限制

音频帧走独立的二进制协议（`packages/protocol/audio-frame.v1.md`）。当前强制执行的规则：

| 规则 | 实现位置 | 违反时行为 |
|---|---|---|
| 只有 `role=audio` 可以发送二进制帧 | `EventConnection._handle_binary` | `system.error{code:"invalid_role"}` + 关闭 1008 |
| `role=audio` 不能下发 `capture.start` / `capture.stop` | `_dispatch` | `invalid_role`，但不中断音频流 |
| 单帧最大 4096 字节 | `AudioConfig.max_frame_bytes` | `invalid_payload` + 关闭 1009 |
| 每秒最多 200 帧、突发 250 | 令牌桶（`AudioSession`） | 计数 `rate_limited`；1 秒内超过 10 次关闭 1008 |
| 1 秒内无效帧超过 25 | `AudioSession.should_disconnect` | `invalid_payload` + 关闭 1008 |
| 帧级错误不中断音频 | `AudioSession.handle` | 丢弃并计数，连接保留 |
| PCM 不落盘、不进日志 | `InMemoryAudioSink` 只计数 | 仓库中不存在写音频文件的代码路径 |

**角色是自报的**：`hello.payload.role` 由客户端声明，服务端只做白名单校验，**不能证明对端是哪个操作系统进程**。持有 `runtime.json` 令牌的同用户进程可以声明自己为 `audio` 并发送帧。

### 短期音频票据（Phase 2-C 已实现）

目标：把"知道令牌"与"被授权发送音频"分开，并给音频通道一个短时效、可撤销、一次性的凭证。

```text
UI(role=ui) --capture.start--> Core
Core: tickets.begin_session() -> sessionId
      ticket = base64(JSON{p:audio, n:nonce, s:sessionId, e:expiry}) . HMAC-SHA256(secret, body)
      spawn sidecar，通过环境变量 RTC_AUDIO_TICKET 传入（不写日志、不进 argv）
audio: hello.payload.ticket = <ticket>
Core: 校验 HMAC -> session -> 未使用 -> 未过期；消费后该 nonce 立即失效
```

实现位置：`copilot_core/tickets.py`（签发/校验）、`capture/controller.py`（会话开始/结束）、
`ws/connection.py`（role=audio 且票据被要求时校验）。协议字段：`HelloPayload.ticket`（可选）。

强制规则：

- 只有 **Core 拉起的 sidecar** 会被要求出示票据：`/healthz` 的 `capture.sidecarPid` 非空期间，
  `role=audio` 的 hello 必须携带有效票据，否则 `ticket_invalid` + 1008。
- 票据有效期默认 60 秒（`capture.ticket_ttl_seconds`），一次性消费，重放返回 `replayed`。
- `capture.stop`、sidecar 崩溃、Core 关闭都会结束会话，旧票据随之失效（session 不同即拒绝）。
- 手工启动的 sidecar（`runtime.json` + 令牌）不受影响：没有活跃会话时不要求票据，
  Phase 2-B 的 CLI 用法与测试脚本保持不变。

**并发与生命周期保证（P2-C.1）**：每次 `capture.start` 递增会话代次（generation）；
状态回报必须匹配当前连接与代次，迟到/跨会话消息一律忽略；`error` 不会被后续迟到消息清除。
所有失败路径（启动/停止超时、sidecar 错误、异常退出、连接断开、Core 关闭）都走同一收尾流程：
取消超时任务 → 撤销票据 → 终止进程并等待退出 → 清空连接 → 广播最终状态。

**残余风险**：票据只证明"这张票是本次 Core 会话签发的"，不证明进程身份。能读取 `runtime.json`
的同用户进程仍可在会话窗口内抢先连接受理；缓解是窗口短（≤60s）、一次性、且 Core 会记录
`audio` 连接与票据消费。真正的进程身份（父进程/PID 校验、命名管道句柄继承）不在本阶段范围。

### 真实采集的隐私边界（Phase 2-B）

- `--capture-loopback` / `--capture-mic` 是**显式启动参数**，没有自动启动、没有后台常驻采集；不传参数时进程只连接 Core、不发音频。
- loopback 采集的是**整个输出设备**，包含会议之外的声音（通知、音乐、其他应用）。产品文案必须说明"整机输出采集"；按进程采集在 Phase 2-C。
- 麦克风默认关闭，必须显式 `--capture-mic`。
- 采集侧只做 float→PCM16 与分帧，不写文件；Core 只在内存中统计电平（`peakDbfs` / `lastPeakDbfs`），不保留 PCM。
- 电平统计是裸数字，不含频谱或可还原音频的信息。

## 8. 残余风险（明确承认）

| 风险 | 说明 | 缓解 |
|---|---|---|
| 同用户进程可读 `runtime.json` | Windows 上同用户的其他程序可以读取该文件并连接 Core | 本阶段接受；令牌每次启动轮换 + ACL + 只绑定回环。控制面（`capture.*`）目前仍是占位实现，尚未产生真实权限 |
| **角色自报，不等于进程身份** | 任何拿到令牌的同用户进程都能声明 `role=audio` 并发送音频帧 | 已实施：只有 `audio` 角色可发二进制帧、速率上限、单帧上限、统计可观测；待实施：§7 的短期 ticket（Phase 2-B） |
| 令牌明文落盘 | 见 §4 的取舍 | 每次启动轮换 + ACL + 回环 |
| 无 TLS | 回环流量不加密 | 数据不出本机；不引入自签证书以免增加运维复杂度 |
| 音频内容可被同机进程读取 | Core 在内存中短暂持有 PCM 帧 | 默认不落盘、不进日志；同用户内存读取属于操作系统级信任边界，本阶段不声称防护 |
| 整机 loopback 会采到非会议声音 | 设备级采集覆盖全部输出 | 由用户显式启动并在 UI 标注；按进程采集在 Phase 2-C |

## 9. 明确不做

**不存在**向会议软件注入文字、模拟键鼠、自动发言的代码路径。`package.json` / `*.csproj` 中没有 `robotjs`、`nut.js`、`SendInput` 封装等依赖；后续阶段也不会加入——这是产品约束，不是待办。

## 10. 资源释放

- WebSocket：连接结束后 `finally` 中关闭；服务端对已消失对端做 best-effort 关闭。
- 音频会话：连接结束、异常或角色拒绝时归还 `AudioHub` 的会话计数，统计保留（`test_audio_disconnect_releases_session` 断言）。
- SQLite：应用 lifespan 结束时 `close()`；WAL 在重启后仍可读。
- 子进程：`scripts/e2e-smoke.ps1` / `scripts/e2e-audio.ps1` 在 `finally` 中终止 Core 与客户端；桌面端 `window-all-closed` 时断开连接并退出。

## 11. 报告问题

这是个人项目；如发现安全问题，请通过 GitHub Issues 描述复现步骤，不要在公开 issue 中粘贴真实令牌或会议内容。
