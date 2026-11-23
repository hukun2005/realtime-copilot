# P51-01 测试报告

- `uv run pytest services/copilot-core/tests/test_answer_runtime.py -q` → 7 passed
- 覆盖：正常流/终态唯一/sequence、主动取消、上游关闭、新问题抢占 generation、
  session 隔离、重复 submit 幂等、provider 失败、cancel_session/cleanup。
- `uv run pytest -q` → 全量结果见 BUILD_LOG。
- Mock only；真实 DeepSeek 未在本阶段调用。
