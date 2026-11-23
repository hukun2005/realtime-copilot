# 模块依赖（审计）

```text
app.asr_events
  -> broadcast(EventConnection.send_event)
  -> TranscriptHandler.handle
  -> QuestionEngine.ingest_final
       -> TranscriptLog / RuleClassifier / FollowUpResolver / QuestionStore

detection/llm.py
  -> DeepSeekClient.complete/stream
  -> DeepSeekReviewer.review
  -> DeepSeekAnswerProvider.answer/stream

detection/streaming.py
  -> AnswerTaskManager(provider.stream)  // 已有取消/抢占，但未接 app

rag/context.py -> knowledge/store.py(KnowledgeStore FTS5)
answer.py -> AnswerContext(question fields)

protocol
  -> schema/events.v1.json -> generated Python/TS -> fixtures
```

## 可直接复用

- `QuestionEngine`（检测/状态/追问）
- `DeepSeekClient.stream`（SSE、错误、取消关闭）
- `DeepSeekAnswerProvider.stream`（AnswerContext → messages）
- `AnswerTaskManager`（generation/task_id 取消）
- `build_rag_context` / `KnowledgeStore.search`
- `EventConnection`（现有 WS 发送）

## 必须新增

- `runtime/answer_runtime.py`：问题 → Review → RAG → Answer → 事件
- `runtime/answer_events.py`：公共字段/sequence/终态
- Review 三态结构化模型与降级
- answer.* Schema + 生成类型 + fixtures
- session 级订阅/有界队列（扩展 ws，不另建总线）

## 禁止

- 新建第二个 DeepSeekClient/AnswerProvider
- 另建第二套 JSON 协议事实源
- 把完整仓库/整段转写拼入 Prompt
