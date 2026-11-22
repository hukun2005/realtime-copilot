# P3.5 Provider 对比（合成 TTS）

- 生成时间：2026-11-22T07:22:50.154721+00:00
- 数据集：`realtime-copilot-p35-synthetic`
- 音频：合成 TTS，不是真实会议；真实会议 WER 仍 `NOT_MEASURED`

| Provider | 状态 | 模型 | CER micro | WER micro | 计分样本 |
|---|---|---|---|---|---|
| aliyun | PASS | paraformer-realtime-v2 | 12.54% | 10.53% | 10 |
| deepgram | NOT_RUN | - | - | - | - |

## 说明

- deepgram：NOT_RUN（DEEPGRAM_API_KEY not set）

> 不得把 NOT_RUN/ERROR/PARTIAL 当作 PASS；也不得把本表当作真实会议识别质量。
