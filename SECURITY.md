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

## 7. 残余风险（明确承认）

| 风险 | 说明 | 缓解 |
|---|---|---|
| 同用户进程可读 `runtime.json` | Windows 上同用户的其他程序可以读取该文件并连接 Core | 本阶段接受；Core 目前只暴露状态与健康检查。Phase 2 起音频控制会要求 `role=audio` 并与进程身份绑定 |
| 令牌明文落盘 | 见 §4 的取舍 | 每次启动轮换 + ACL + 回环 |
| 无 TLS | 回环流量不加密 | 数据不出本机；不引入自签证书以免增加运维复杂度 |
| 无速率限制 | 本阶段连接数极少 | Phase 2 前加入每连接消息速率上限 |

## 8. 明确不做

**不存在**向会议软件注入文字、模拟键鼠、自动发言的代码路径。`package.json` / `*.csproj` 中没有 `robotjs`、`nut.js`、`SendInput` 封装等依赖；后续阶段也不会加入——这是产品约束，不是待办。

## 9. 资源释放

- WebSocket：连接结束后 `finally` 中关闭；服务端对已消失对端做 best-effort 关闭。
- SQLite：应用 lifespan 结束时 `close()`；WAL 在重启后仍可读。
- 子进程：`scripts/e2e-smoke.ps1` 在 `finally` 中终止 Core 与客户端；桌面端 `window-all-closed` 时断开连接并退出。

## 10. 报告问题

这是个人项目；如发现安全问题，请通过 GitHub Issues 描述复现步骤，不要在公开 issue 中粘贴真实令牌或会议内容。
