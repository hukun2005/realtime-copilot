# P3.5 ASR Segment 延迟（合成 TTS）

- 生成时间：2026-11-22T07:26:29.422705+00:00
- 数据集：`realtime-copilot-p35-synthetic`
- 口径：从调用 transcribe 到首个 partial/final；不是面试回答延迟

| Provider | 状态 | 模型 | 有 final | Final P50 | Final P95 | Finalization P50 | Finalization P95 |
|---|---|---|---|---|---|---|---|
| aliyun | PASS | paraformer-realtime-v2 | 10 | 1404.9 | 4126.2 | 771.9 | 2228.2 |
| deepgram | NOT_RUN | - | - | - | - | - | - |

## aliyun 逐样本（毫秒）

| 样本 | 类别 | 音频秒 | 首 partial | final | finalization | finals | 错误 |
|---|---|---|---|---|---|---|
| clean_01 | clean | 6.33 | 1160.3 | 1561.7 | 928.7 | 1 | - |
| clean_02 | clean | 5.11 | 844.4 | 1121.3 | 610.3 | 1 | - |
| technical_01 | technical | 7.14 | 1154.8 | 2048.8 | 1334.3 | 1 | - |
| numbers_01 | numbers | 6.65 | 1083.5 | 1354.8 | 689.8 | 1 | - |
| mixed_01 | mixed | 5.28 | 866.3 | 1109.4 | 581.9 | 1 | - |
| filler_01 | filler | 5.47 | 888.6 | 1147.3 | 600.3 | 1 | - |
| long_01 | long | 18.98 | 2995.4 | 4126.2 | 2228.2 | 1 | - |
| clean_01_snr15 | noisy | 6.33 | 1030.7 | 1406.0 | 773.0 | 1 | - |
| clean_01_snr5 | noisy | 6.33 | 1038.0 | 1404.9 | 771.9 | 1 | - |
| technical_01_snr10 | noisy | 7.14 | 1160.1 | 1646.9 | 932.4 | 1 | - |
| silence_01 | silence | 3.00 | - | - | - | 0 | - |

- deepgram：NOT_RUN（DEEPGRAM_API_KEY not set）

> Final 从提交整段音频算起；Finalization 减去上传耗时估计（100ms 音频/10ms 间隔 ≈ 10x 实时）。
> 样本量很小（<=11），P95 接近最大值；不得把 TTS 延迟当作真实会议/面试回答延迟。
