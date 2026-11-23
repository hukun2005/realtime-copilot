# P51-03 Review 设计

- `ReviewOutcome` 是唯一结构化结果，Pydantic `extra=forbid`。
- 决策：confirmed / rejected / uncertain / unavailable。
- 降级：超时、网络异常、非法 JSON 一律 unavailable，采用规则检测结果；不会变成空答案。
- rejected 不进入 Answer；uncertain 仅在高规则置信度时继续。
- `deepseek_review_call` 复用现有 `DeepSeekClient.complete`；真实调用仍由 P51-05 装配。
- 运行时通过可选 reviewer 接入；未配置时行为与 P51-01 一致。
