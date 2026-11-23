# P51-04 RAG 设计

- RagContextBuilder 包装 build_rag_context，不复制检索实现。
- project_evidence / technical_knowledge 分字段；citations 保留 repo/commit/file/行号。
- 问句全文无命中时用关键词 token 回退。
- 检索异常降级为 missing=rag_failed；不伪造项目证据。
- Runtime 的 context_factory 支持同步或异步；P51-05 可注入。
