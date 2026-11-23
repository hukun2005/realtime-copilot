# P51-05 SSE Answer 设计

- 复用 DeepSeekClient.stream / DeepSeekAnswerProvider.stream。
- `build_answer_stack` 返回 `AnswerStack{runtime, client}`；close 释放任务与 HTTP 连接。
- Review 在 SSE 前执行；rejected 不发起模型请求。
- SSE 只产生内部 RuntimeEvent；P52 再映射 answer.* 统一协议并发布 WS。
- 取消：runtime cancel -> asyncio task cancel -> async generator close -> HTTP 响应关闭。
