# 协议兼容性审计

- 唯一事实源：`packages/protocol/schema/events.v1.json`
- answer 事件：started/delta/completed/cancelled/failed，含 sessionId/questionId/generation/sequence。
- 控制事件：answer.subscribe/unsubscribe（requestId + sessionId）。
- Python/TS 均由 Schema 生成；fixtures 覆盖。
- `EVENT_TYPES` 已含 answer.*；`classifyIncoming` 基于同一 union。

## 桌面端问题

- CoreConnection 只窄化 system.ready/system.error/ping；没有 answer case。
- 没有导入 AnswerEvent 类型到 bridge/preload/Vue。
- 没有 IPC 传输层类型。

## 兼容性结论

Schema/生成类型 **IMPLEMENTED**；
桌面端消费 **NOT_IMPLEMENTED**；
跨语言 fixtures 测试通过，但“Electron 消费”未验证。
