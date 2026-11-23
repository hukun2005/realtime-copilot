# Core API 清单（P54-00）

## HTTP

- GET /healthz（core/audio/transcript/detection 状态）
- GET /api/knowledge/search
- POST /api/rag/context
- 简历 CRUD/激活/项目 PATCH

**不存在**：
- 音频设备/开始/停止 HTTP API
- Answer cancel/session cancel
- Answer 任务快照
- DeepSeek 健康检查 HTTP

## WebSocket

- hello 鉴权、system.ready、ping/pong
- capture.start/stop（客户端命令）
- answer.subscribe/unsubscribe（ui/bridge）
- answer.started/delta/completed/cancelled/failed（服务端事件）

## Runtime

`answering.AnswerRuntime` 支持 submit/cancel/cancel_session/status/cleanup/close。
`app.py` 在有 DEEPSEEK_API_KEY 时装配 AnswerStack；无 Key 时 answer_runtime=None。

## 捕获生命周期

`on_capture_start`：创建 sess_*，启动 transcript + ASR。
`on_capture_stop`：停 ASR、停 transcript；**未** cancel_session，也未清空 Publisher 订阅。
shutdown：关闭 AnswerStack。

## 结论

Runtime 已具备取消能力，但没有暴露给桌面端；采集停止与答案任务未联动。
