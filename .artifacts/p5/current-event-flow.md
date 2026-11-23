# 当前 ASR Final 事件流

```text
C# AudioCapture
  -> AudioFrame v1 (WS binary)
  -> AudioHub/VAD
  -> AsrCoordinator
  -> AsrManager final
  -> app.asr_events(asr.final)
       -> broadcast 给所有 UI 连接
       -> TranscriptHandler.handle (转写落库/transcript.segment)
       -> QuestionEngine.ingest_final
            -> question/detection 返回后丢弃
```

## 答案链路实际不存在

```text
QuestionEngine -> (断开)
Review         -> 无调用方
RAG            -> 无调用方
DeepSeek SSE   -> 仅脚本/单测调用
answer.* 协议  -> Schema 中不存在
Electron       -> 只接收 core/audio/asr/transcript 事件
```

## 取消机制现状

- 客户端级：`AnswerTaskManager.cancel()` 会 cancel asyncio task；`DeepSeekClient.stream` 的 `async with` 负责关闭响应。
- 运行时级：没有 request_id/session 级任务表；`app` 没有取消 API。
- 跨问题抢占：只有 `AnswerTaskManager` 单实例，未与 session/generation 绑定。

## 风险

- ASR final 重复投递：TranscriptLog 去重依赖 segment_id；跨来源独立，但无 event_id 去重。
- 事件广播失败会移除连接，但没有重连/重放；answer 终态不能依赖这种“尽力而为”。
