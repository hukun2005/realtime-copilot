# P3.5 ASR 评测数据集

本目录只包含**合成 TTS** 基线、确定性噪声与静音控制样本，用于可复现的识别质量
评测。**真实会议录音不进入仓库。**

## 目录

```
evaluation/
  dataset/
    audio/*.wav        # 16 kHz / 16-bit / mono
    manifest.json      # 参考文本、类别、哈希、时长、条件
  local/               # 本地真实会议录音（.gitignore，不提交）
```

## 数据集组成

| 类别 | 样本 | 目的 |
|---|---|---|
| clean | clean_01, clean_02 | 清晰中文会议句 |
| technical | technical_01 | Vue3 / TypeScript / Python / SQLite 术语 |
| numbers | numbers_01 | 金额、百分比、数字 |
| mixed | mixed_01 | 中英混合、API / P95 |
| filler | filler_01 | 口语填充词、语气词 |
| long | long_01 | 多句长段落 |
| noisy | clean_01_snr15/5、technical_01_snr10 | 确定性白噪声，SNR 5/10/15 dB |
| silence | silence_01 | 无语音控制，检查幻觉/误触发 |

所有语音由 Windows `System.Speech` 的 `Microsoft Huihui` 本地合成，不是真人，
也不是会议录音。

## Manifest 字段

- `id` / `file` / `category` / `language`
- `durationSeconds` / `sampleRate` / `channels` / `bitsPerSample`
- `sha256`：文件字节哈希，评测前校验
- `reference`：人工确认的参考文本（静音为空）
- `conditions`：`noise`、`snrDb`、`speech`

## 重新生成

```powershell
pwsh -File scripts/build-eval-dataset.ps1
uv run python scripts/build_eval_manifest.py
```

第二步会重新生成噪声样本、静音样本和 manifest（含新哈希）。TTS 输出随系统语音
版本可能略有差异；提交前应重新跑评测并更新报告。

## 真实会议录音（本地）

1. 取得参会者授权，记录来源与用途。
2. 放到 `evaluation/local/`，不要 `git add`。
3. 在本地 manifest 中登记 `reference`（人工校对）、时长、类别 `meeting`、授权说明。
4. 报告中只写文件哈希、时长、类别与指标，不写会议正文。

真实会议 WER 在未实际评测前必须标记 `NOT_MEASURED`，不能用 TTS 结果替代。

## 评测口径

- 中文：CER（字符错误率，去标点/空白）
- 英文：WER（词错误率）
- 延迟：Segment 首 partial / final 延迟，与端到端回答延迟分开报告
- 稳定性：TTS 30/60 分钟仅代表链路稳定性，不代表会议识别质量

工具与报告在 P35-02 起实现。


## CER/WER 工具

```powershell
# 工具自检（用参考文本当假设，不是 ASR 结果）
uv run python scripts/evaluate_asr.py --oracle --label oracle-selfcheck

# 评测真实 ASR 假设
uv run python scripts/evaluate_asr.py `
  --hypotheses evaluation/reports/hypotheses-paraformer.json `
  --source paraformer --label paraformer-clean
```

输出：

- `evaluation/reports/cer-wer-<label>.json`
- `evaluation/reports/cer-wer-<label>.md`

报告默认不写参考/假设正文；真实会议评估只保留样本 id、类别与指标。


## Provider 对比（P35-03）

```powershell
uv run python scripts/eval_providers.py --providers aliyun deepgram
```

输出：

- `reports/hypotheses-<provider>.json`
- `reports/cer-wer-<provider>.{json,md}`
- `reports/provider-comparison.{json,md}`

缺 Key 的 Provider 标记 `NOT_RUN`，不会生成指标。当前 Aliyun（合成 TTS）CER 12.54%，
Deepgram 无 Key 为 `NOT_RUN`；这是合成集结果，不是真实会议质量。

数字类别受“中文数字 vs 阿拉伯数字”影响（如“一千二百三十四” vs `1234`），
当前 CER 未做数字 ITN，报告需同时看文本假设。


## 延迟（P35-04）

```powershell
uv run python scripts/eval_latency.py --providers aliyun deepgram
```

输出 `reports/latency-comparison.{json,md}`。

- `firstPartialMs` / `finalMs` 从整段音频提交开始；
- `finalizationMs` 减去上传耗时估计（约 10x 实时发送）；
- 不是面试回答延迟；真实会议流式延迟仍需单独测量。


## 长时间稳定性（P35-05）

- `reports/stability-30min.json`：30 分钟单来源
- `reports/stability-60min-dual.json`：60 分钟双来源

均为 TTS 合成音频，只证明链路稳定性，不代表真实会议识别质量。
