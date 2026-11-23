# P5.1 + P5.2 实施计划

## P51-01 Answer Runtime
新增 `copilot_core/runtime/`：
- `answer_runtime.py`：`submit_question(question, session_id, project_id)`、`cancel(request_id)`、`cancel_session(session_id)`、`status(request_id)`。
- `models.py`：TaskState/request_id/generation/timings。
- `answer_context.py`：扩展 request_id/citations/recent transcript。
- `answer_events.py`：构造 answer.* 公共字段/sequence/终态。
- `cancellation.py`：token + task 取消。
测试：生命周期、并发隔离、重复 submit、取消、清理。

## P51-02 ASR Final
在 `app.asr_events` 消费 `ingest_final` 返回值；空/纯标点/过短过滤；来源隔离；event_id/segment_id 去重；非 final 不触发。测试 app 集成。

## P51-03 Review
新增 Pydantic ReviewDecision（decision/confidence/type/reason/normalized）。
超时/非法 JSON 降级为规则结果并标记 unavailable；rejected 不启动 Answer；uncertain 按规则置信度。

## P51-04 RAG
调用 `build_rag_context(query, store, project_id)`；转 AnswerContext；保留 citations/missing；检索失败只保留通用知识，不让任务崩溃。

## P51-05 SSE
用现有 `DeepSeekAnswerProvider.stream`；started→delta*→completed/cancelled/failed；sequence 递增；终态唯一；新问题取消旧任务。

## P52-01..02 Schema
扩展 `events.v1.json` 的 `oneOf`：answer.started/delta/completed/cancelled/failed；生成 Python/TS，更新 fixtures/EVENT_TYPES/跨语言测试。禁止手改生成文件。

## P52-03 Publisher
在现有 WS 上增加 session 订阅 + 每订阅者有界队列；delta 可合并，终态不可静默丢；满队列记录指标/关闭连接。

## P52-04 WS
认证沿用现有 token；session 订阅鉴权；限制消息/连接/订阅数；重连重订阅 + sequence 快照；不把 token 放 URL。

## P52-05 Frontend
`answer-event-client.ts` + `answer-store.ts`；按 request/generation/sequence 去重；状态 idle/running/completed/cancelled/failed。

## 测试/验收
单元（Runtime/Review 降级/RAG/SSE/取消/抢占/背压/WS 认证）→ 集成（Fake ASR→Fake Review→Fake RAG→Fake SSE→WS→store）→ 真实 DeepSeek 冒烟（无 Key 则 NOT_RUN）→ 端到端合成问题。

## 风险
- 现有 `ReviewDecision` 无三态，需新增模型而非改坏旧 review。
- EventConnection 全局广播需扩展，不另建总线和重复连接管理。
- answer Schema 变更必须同步 Python/TS/C# 消费者与 fixtures。
