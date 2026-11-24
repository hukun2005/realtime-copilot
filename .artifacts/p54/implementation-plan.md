# P54 实施计划

## P54-01 Electron 事件客户端
- CoreConnection 增加 answer 监听器集合、subscribe/unsubscribe、answer.* switch。
- 认证 ready 后调用 `answer.subscribe`（当前 active session 来源需 Core 提供；若无，先只订阅 hello 后由 UI 触发）。
- 重连：有界退避 1/2/5/10/30s，单循环，退出清理；重订阅；无快照时标记 partial。
- 测试 core-connection-answer.test.ts。

## P54-02 IPC + Vue
- bridge/preload/main 增加 `onAnswerEvent`（固定 channel）。
- App.vue 创建 single AnswerStore，订阅/清理；用 reactive 包装。
- 测试 preload bridge 与 store 集成。

## P54-03 UI
- 重构首页：状态栏、采集控制、转写、问题、答案、历史。
- 需要 Core 暴露 capture 控制 API（当前无）；若无法新增，UI 只能展示，不能假装可开始。
- 四种回答模式需后端 Prompt 字段支持；当前 AnswerContext 无 mode，需 P54-05/后端扩展，禁止只改标签。

## P54-04 生命周期
- `on_capture_stop` 取消当前 session 的 runtime 任务。
- 增加 WS `answer.cancel` 或 HTTP cancel（需协议扩展）。
- 订阅清理；任务上限/定期 cleanup。
- 测试停止采集/取消互不影响。

## P54-05 DeepSeek
- HTTP 402/401/429/超时分类；health_check 暴露状态（不返回 Key）。
- 不在 402/401 自动重试；429/5xx 有界退避。
- 真实 API 仍 FAILED(402)，需用户充值后复测。

## P54-06 E2E
- Mock：Fake ASR final → WS subscribe → Electron CoreConnection → IPC → AnswerStore。
- 真实：需有效 Key；否则 LIVE_VERIFIED=NOT_RUN/FAILED。
- 桌面 smoke 需 Electron + Core + 音频权限。

## P54-07 发布
- 构建/打包/文档；当前未审计打包脚本，先不承诺安装包。
