# RealTime Copilot

Windows 实时 AI 会议助手：采集电脑输出音频（可选麦克风）→ 流式语音识别 → 识别问题 → DeepSeek 流式生成口语化答案 → 桌面悬浮窗展示。支持 Harness 集成模式与独立运行模式。

## 当前状态

**阶段 0（架构审查与设计）已完成，业务代码尚未开始。** 本目录当前只有设计与计划文档，没有可运行实现，也没有任何延迟数据。

| 文档 | 内容 |
|---|---|
| [ARCHITECTURE_REVIEW.md](ARCHITECTURE_REVIEW.md) | 现有 DeepSeek Harness 仓库的事实审查、可复用模块、风险、未知问题 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 目标架构：进程拓扑、Copilot Core、双模式、数据流、安全与隐私模型 |
| [PLUGIN_API.md](PLUGIN_API.md) | Harness 插件契约、Copilot Provider 接口、事件协议、运行时配置 |
| [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) | 十个阶段的文件、任务、依赖、验收标准、测试命令 |
| [TEST_PLAN.md](TEST_PLAN.md) | 测试分层、性能测量协议、安全测试、Harness 兼容矩阵 |
| [BUILD_LOG.md](BUILD_LOG.md) | 本轮审查的输入、动作、证据、决策与待验证项 |

## 阅读顺序

1. 先读 `ARCHITECTURE_REVIEW.md` 的事实与风险部分——它决定了后面的设计为什么不采用某些看起来更直接的方案。
2. 再读 `ARCHITECTURE.md` 的「双模式」与「延迟预算」两节。
3. 开发者按 `IMPLEMENTATION_PLAN.md` 的阶段推进，每个阶段以 `TEST_PLAN.md` 中对应的验收门禁结束。

## 性能目标（尚未达成，也未测量）

以「问题最后一个有效音频片段结束」为锚点，首批答案 P50 < 1s、P95 < 2s。该目标目前只是设计约束，`TEST_PLAN.md` 给出测量协议；在 Phase 5 之前不会产生任何真实数字。任何文档、提交信息或汇报都不得把它写成已实现的结果。
