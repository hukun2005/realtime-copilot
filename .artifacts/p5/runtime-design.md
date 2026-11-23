# P51-01 Answer Runtime 设计

## 已实现

`copilot_core/answering/`：
- `AnswerTask`：request_id/session/question/generation/state/sequence/text。
- `RuntimeEvent`：内部事件（P52 再映射统一协议）。
- `AnswerRuntime`：
  - `submit_question(question, session_id, project_id)` → request_id
  - `cancel(request_id, reason)`
  - `cancel_session(session_id)`
  - `status(request_id)`
  - `cleanup(keep)`
- 每 session 仅一个 active request；新问题取消旧任务，generation 递增。
- 同一 question_id 活跃时重复 submit 幂等返回原 request_id。
- 事件序列 started → delta* → completed/cancelled/failed；sequence 每请求递增。
- 终态唯一；provider 异常发布 failed，不传播到 Core。
- 取消会 cancel asyncio task，异步生成器 finally 释放上游连接。
- 发布者异常被计数，不杀死任务（`publish_errors`）。

## 未实现（后续阶段）

- Review/RAG 接入（context_factory 已预留）。
- ASR Final/app 接线。
- P52 Schema/WebSocket session 订阅/前端 store。
- 真实 DeepSeek 运行时冒烟。
