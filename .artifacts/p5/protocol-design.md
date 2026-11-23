# P52-01 answer 协议设计

## 事实源

`packages/protocol/schema/events.v1.json`（唯一）。

## 公共信封

沿用 v/eventId/ts/requestId；answer 事件额外强制：

- sessionId (`sess_*`)
- questionId (`q_*`)
- generation (>=0)
- sequence (>=1，单 request 内递增)

## 事件与 payload

| type | payload |
|---|---|
| answer.started | question, questionType, status="running" |
| answer.delta | delta |
| answer.completed | answer, finishReason, usage(nullable) |
| answer.cancelled | reason: user_cancelled/superseded/session_stopped/shutdown/timeout |
| answer.failed | code, message, retryable |

## 兼容性

- 协议版本仍为 v1；普通事件 required 不变。
- answer 事件为新增类型，旧客户端按 unsupported_event 处理。
- 终态唯一/sequence 由运行时保证；Schema 只约束结构。
