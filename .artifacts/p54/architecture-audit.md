# P54-00 架构审计

- 基线：`d1b37d2`（与 origin/main 一致）
- 分支：`feature/p54-desktop-realtime-closure`
- 工作区：干净
- 基线测试：Python 545 passed / 3 skipped；桌面 15 passed；typecheck 通过。
- 真实 DeepSeek：**FAILED / HTTP 402**（见 .artifacts/p5/live-api-report.md）。

## 状态总表

| 能力 | 状态 | 证据 |
|---|---|---|
| Answer 协议（5 类 + subscribe/unsubscribe） | IMPLEMENTED | packages/protocol/schema/events.v1.json |
| Answer Runtime / Review / RAG / SSE | IMPLEMENTED/UNIT_TESTED | services/copilot-core/src/copilot_core/answering |
| Publisher 有界队列/session 订阅 | IMPLEMENTED/UNIT_TESTED | answering/publisher.py |
| WS 订阅协议事件 | IMPLEMENTED/INTEGRATION_TESTED | ws/connection.py；test_ws_answer.py |
| Electron 消费 answer 事件 | NOT_IMPLEMENTED | core-connection.ts 只处理 ready/error/ping |
| Electron answer IPC | NOT_IMPLEMENTED | bridge.ts 无 onAnswerEvent |
| Vue AnswerStore 接入 | PARTIAL（Store 存在，页面未使用） | renderer/services/answer-store.ts |
| 实时转写/问题/答案 UI | NOT_IMPLEMENTED | App.vue 只有状态卡 + 简历 |
| 音频开始/停止 UI | NOT_IMPLEMENTED | 无 capture 控制 IPC |
| 停止采集取消答案任务 | NOT_IMPLEMENTED | app.py on_capture_stop 不调用 runtime.cancel_session |
| 重连/快照恢复 | NOT_IMPLEMENTED | core-connection 无退避/重订阅 |
| DeepSeek 健康状态 | PARTIAL | DeepSeekClient.health_check 存在，无 HTTP/UI 接入 |
| 真实 DeepSeek → WS → Electron | NOT_RUN | 402 |
| 真实桌面 E2E | NOT_RUN | 未启动 Electron + 真实 Core |

## 关键断点

1. `CoreConnection.handleMessage` 的 switch 没有 answer.* 分支，也不发送 answer.subscribe。
2. `main.ts` 没有 `copilot:answer-event` IPC；preload/bridge 没有 onAnswerEvent。
3. `AnswerStore` 没有实例化/绑定到 App.vue。
4. 没有实时转写/问题/答案组件；没有音频开始/停止控制。
5. `on_capture_stop` 只停 ASR/Transcript，不取消 Runtime 任务。
6. 没有任务快照/取消 API 供重连恢复。
7. DeepSeek 健康检查未暴露为状态接口。

## 结论

后端事件链基本具备；桌面端从 WebSocket 到 Vue 的消费链完全缺失。P54-01/02/03 是主路径，P54-04/05 是可靠性。
