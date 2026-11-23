# Electron 事件流审计

## 现状

```text
CoreConnection.connect()
  -> ws connect
  -> hello(token, role=ui)
  -> system.ready -> reduceCoreStatus
  -> ping -> pong
  -> system.error -> error + close
  -> close/error -> status
```

`handleMessage` 对 `answer.*` 走 default 忽略；没有发送 `answer.subscribe`。

## IPC

`main.ts` 仅注册 snapshot/audio/resumes/reconnect 等；无 answer channel。
`preload.ts`/`bridge.ts` 无 onAnswerEvent。

## Store

`renderer/services/answer-store.ts` 已实现 session/request/generation/sequence 去重，
但 `App.vue` 没有 import/实例化，也没有任何事件源。

## 重连

无指数退避、无重订阅、无任务快照；只有手动 reconnect。

## 断点结论

WebSocket answer 事件能到达主进程 socket 回调，但：
1. 没有 subscribe → Core 不会为该连接发布；
2. 即便收到，也没有 IPC/Store 消费。

因此“Electron 已消费答案事件”为 **NOT_IMPLEMENTED**。
