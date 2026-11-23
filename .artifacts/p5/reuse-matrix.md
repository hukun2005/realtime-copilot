# 复用矩阵

| 能力 | 现有实现 | P5 用法 | 缺口 |
|---|---|---|---|
| ASR final | app.asr_events + AsrManager | 触发 runtime | 无 event_id 去重 |
| 问题检测 | QuestionEngine.ingest_final | 直接调用 | 返回值未消费 |
| Review | DeepSeekReviewer.review | 接入并结构化 | 无 confirmed/rejected/uncertain；无超时降级 |
| AnswerContext | detection/answer.py | 扩展 request_id/citations | 字段不全 |
| RAG | build_rag_context | 自动检索 | 未接运行时 |
| Knowledge | KnowledgeStore.search | 检索证据 | 无索引写入 API |
| SSE | DeepSeekClient.stream | 逐块发布 | 无事件协议 |
| 取消 | AnswerTaskManager | session/generation 包装 | 无运行时任务表/取消 API |
| 事件 | EventConnection.broadcast | 按 session 订阅 | 无队列/sequence/终态唯一 |
| 协议 | events.v1.json | 新增 answer.* | 无 answer Schema/生成类型 |

## 结论

复用以上模块；新增 `runtime/` 只做编排与事件，不复制 LLM/知识库实现。
