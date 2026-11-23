# @realtime-copilot/protocol

有线协议 v1 的唯一事实源与双语言绑定。

## 内容

| 路径 | 说明 |
|---|---|
| `schema/events.v1.json` | JSON Schema（draft 2020-12），定义信封与 19 类事件 |
| `src/generated/types.ts` | 由 schema 生成的 TypeScript 类型（勿手改） |
| `python/copilot_protocol/models.py` | 由 schema 生成的 Pydantic v2 模型（勿手改） |
| `python/copilot_protocol/envelope.py` | 手写运行时：解析、分类、构造、序列化 |
| `src/index.ts` | 手写运行时：`classifyIncoming`、`makeEvent`、ID 生成 |
| `fixtures/` | 跨语言往返测试样本（含 `invalid/`） |
| `scripts/generate.mjs` | 生成器：`json-schema-to-typescript` + `datamodel-code-generator` |

## 修改协议

```bash
# 1) 改 schema/events.v1.json
# 2) 重新生成（需要 pnpm 与 uvx）
pnpm -C packages/protocol generate
# 3) 跑测试
pnpm -C packages/protocol test
uv run pytest packages/protocol/python/tests -q
```

CI 会执行 `generate` 后比对 `git diff`：schema 与生成代码不同步会直接失败。

## 事件

`hello`、`system.ready`、`system.error`、`ping`、`pong`、`audio.status`、`capture.start`、`capture.stop`。

信封字段固定为 `v` / `type` / `eventId` / `requestId?` / `ts` / `payload`；`v` 必须等于 `1`，`type` 是封闭枚举。

## 兼容规则

- 新增可选字段：不升版本，客户端按缺省处理。
- 删除字段、改变语义、收缩枚举：必须升 `v=2`。
- 收到未知 `type`：忽略（前向兼容）。
- 版本不匹配 / 信封结构错误：回 `system.error` 并关闭连接。

## 测试

`pnpm -C packages/protocol test` 覆盖：fixtures 全通过、8 类事件往返无字段丢失、非法样本被分类、信封字段一致性、ID 生成格式与唯一性。
