# 生命周期缺口

## 停止采集

`app.py on_capture_stop`：
1. asr_coordinator.stop()
2. transcript handler.stop(capture_stopped)
3. **没有** answer_runtime.cancel_session(session_id)
4. Publisher 订阅仍存在，后续无任务但订阅未释放

## 用户取消

- Runtime 有 cancel(request_id)，但无 WS/HTTP 入口，桌面端无法取消。
- 停止生成按钮不存在。

## 重连

- CoreConnection 无退避/重连循环。
- 无 answer.subscribe 恢复。
- 无任务快照；断线期间事件不可恢复。
- 无 sequence 快照请求。

## 清理

- Runtime cleanup 只在显式调用；无定期清理。
- AnswerStore 只有最近 10 个 request 列表，但无定时/容量清理入口。
- Publisher 订阅随连接关闭释放（已实现）。

## 风险

- 长会话任务 Map 增长。
- 停止采集后 UI 仍可能显示“生成中”。
- 新会话可能使用旧 session 订阅（当前桌面未实现订阅，暂无实际串流）。
