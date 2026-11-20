# copilot-core

RealTime Copilot 的核心服务。Phase 1 只提供协议、鉴权、心跳、配置、日志与数据库骨架；音频采集、ASR 与问答在后续阶段接入。

## 端点

| 端点 | 说明 |
|---|---|
| `GET /healthz` | 200 正常 / 503 数据库降级；返回版本、协议、模式、连接数、角色计数 |
| `WS /ws/v1/events` | 事件通道；首帧必须是 `hello` |

自动文档（`/docs`、`/openapi.json`）已关闭。

## 启动

```bash
uv run python -m copilot_core
# 或安装后的入口：copilot-core
```

启动后写入 `runtime.json`（数据目录下），包含 `wsUrl`、`token`、`port`、`mode`、`version`；退出时删除。

## 配置

`config.toml`（数据目录，Windows 默认 `%LOCALAPPDATA%\RealTimeCopilot`）：

```toml
mode = "standalone"

[server]
host = "127.0.0.1"   # 只接受这个值
port = 0             # 0 = 系统分配
auth_timeout_ms = 2000

[logging]
level = "info"
```

环境变量覆盖：`RTC_MODE`、`RTC_SERVER_PORT`、`RTC_SERVER_AUTH_TIMEOUT_MS`、`RTC_SERVER_HEARTBEAT_INTERVAL_MS`、`RTC_SERVER_HEARTBEAT_TIMEOUT_MS`、`RTC_LOG_LEVEL`、`RTC_STORAGE_PATH`、`RTC_CAPTURE_FRAME_MS`、`RTC_CAPTURE_MIC_ENABLED`、`RTC_CAPTURE_LOOPBACK_ENABLED`、`RTC_DATA_DIR`、`RTC_CONFIG_PATH`。

非法值在启动期失败：`configuration error: <path>: <message>`，退出码 2。

## 目录

```text
src/copilot_core/
├── app.py            # FastAPI 装配、/healthz、WS 路由
├── config.py         # TOML + 环境变量 + 校验
├── runtime.py        # 先绑定 socket 再启动 uvicorn（端口可信）
├── security.py       # 令牌、文件加固、runtime.json
├── logging_setup.py  # JSON Lines + 脱敏
├── errors.py         # CopilotError / ConfigError
├── storage/          # SQLite 连接与 user_version 迁移
└── ws/               # 握手、心跳、事件分发
```

## 测试

```bash
uv run pytest services/copilot-core/tests -q
```

集成测试使用真实 uvicorn 监听回环端口 + 真实 WebSocket 客户端（无 mock 连接）。
