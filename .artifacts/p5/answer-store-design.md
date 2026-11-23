# P52-05 前端 Answer Store

- 纯 TS 状态机，无 DOM/WS 依赖。
- key: sessionId → requests[requestId] → text/sequence/state。
- 拒绝旧 generation、重复/乱序 sequence、终态后的 delta。
- 保留最近 10 个 request。
- 未接 UI；后续悬浮窗直接订阅 store。
