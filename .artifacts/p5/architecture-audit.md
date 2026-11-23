# P51-00 架构审计

- 基线：main `a4e534c`（P5 分支开始前）
- 分支：`feature/p5-realtime-answer-runtime`
- 基线测试：`uv run pytest -q` 首次 1 failed / 499 passed，重跑 500 passed / 3 skipped。
  首次失败未留日志，暂标记为**疑似 flaky，待 CI/重复运行确认**，不能当作已修复。
- 未合并 PR #36（GitHub 元数据 API）保持 OPEN，未带入本分支。

## 真实入口

`services/copilot-core/src/copilot_core/app.py`：
- `asr_events(event)`：先 `broadcast(event)`，再 `transcript_handler.handle(event)`；
  仅 `asr.final` 调 `app.state.detection.ingest_final(...)`。
- 该调用返回值 `(question, detection)` 被**丢弃**；没有 Review/RAG/Answer 调用。
- `ui_connections: set[EventConnection]`，`broadcast` 遍历发送；无 session 订阅、无队列、无背压。
- `EventConnection.send_event` 位于 `ws/connection.py:163`。

## 已有模块

| 模块 | 路径 | 现状 |
|---|---|---|
| QuestionEngine | `detection/engine.py` | final 检测/追问/状态；返回 question+detection |
| Review | `detection/llm.py` | `DeepSeekReviewer.review()`，单条、非结构化 Pydantic 统一模型 |
| Answer | `detection/llm.py` | `DeepSeekAnswerProvider.stream()`；`complete()` 仍保留 |
| 取消 | `detection/streaming.py` | `AnswerTaskManager` generation/task_id，新任务取消旧任务 |
| AnswerContext | `detection/answer.py` | 已有字段，但 `request_id`/citations/recent transcript 不完整 |
| RAG | `rag/context.py` | `build_rag_context()`；项目/技术知识分离 |
| Knowledge | `knowledge/store.py` | FTS5，可过滤 project/repo/commit/source |
| 协议 | `packages/protocol/schema/events.v1.json` | **没有 answer.* 事件** |

## 关键断点

1. ASR final → detection 后没有运行时消费者。
2. Review 未接入；`ReviewDecision` 没有 confirmed/rejected/uncertain 三态。
3. RAG 未接入 QuestionEngine。
4. DeepSeek SSE 只有客户端级流；没有 Core 事件发布。
5. WebSocket 是全局广播，不是按 session 订阅；无 sequence/终态唯一性保障。
6. 前端无 answer store/客户端。

## 审计结论

本阶段必须新增 `runtime/` 编排层与 answer 协议；复用现有 QuestionEngine、DeepSeekClient、DeepSeekAnswerProvider、build_rag_context、KnowledgeStore、EventConnection，不重建客户端。
