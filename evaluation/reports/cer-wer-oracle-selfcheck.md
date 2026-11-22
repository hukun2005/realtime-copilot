# ASR CER/WER：oracle-selfcheck

- 数据集：`realtime-copilot-p35-synthetic` v1
- 假设来源：oracle (self-check, not ASR)
- 生成时间：2026-11-22T07:17:29.468853+00:00
- 缺失样本：0

## 汇总

| 指标 | 值 |
|---|---|
| CER micro | 0.00% |
| CER macro | 0.00% |
| WER micro | 0.00% |
| WER macro | 0.00% |
| 计分样本 | 10 / 11 |
| 静音幻觉 | 0 |

## 分类

| 类别 | 样本 | CER micro | CER macro |
|---|---|---|---|
| clean | 2 | 0.00% | 0.00% |
| filler | 1 | 0.00% | 0.00% |
| long | 1 | 0.00% | 0.00% |
| mixed | 1 | 0.00% | 0.00% |
| noisy | 3 | 0.00% | 0.00% |
| numbers | 1 | 0.00% | 0.00% |
| technical | 1 | 0.00% | 0.00% |

## 逐样本

| ID | 类别 | CER | WER | 错误/参考 | 幻觉 |
|---|---|---|---|---|---|
| clean_01 | clean | 0.00% | 0.00% | 0/23 | 否 |
| clean_02 | clean | 0.00% | 0.00% | 0/17 | 否 |
| technical_01 | technical | 0.00% | 0.00% | 0/39 | 否 |
| numbers_01 | numbers | 0.00% | 0.00% | 0/25 | 否 |
| mixed_01 | mixed | 0.00% | 0.00% | 0/21 | 否 |
| filler_01 | filler | 0.00% | 0.00% | 0/16 | 否 |
| long_01 | long | 0.00% | 0.00% | 0/69 | 否 |
| clean_01_snr15 | noisy | 0.00% | 0.00% | 0/23 | 否 |
| clean_01_snr5 | noisy | 0.00% | 0.00% | 0/23 | 否 |
| technical_01_snr10 | noisy | 0.00% | 0.00% | 0/39 | 否 |
| silence_01 | silence | 0.00% | 0.00% | 0/0 | 否 |

> CER 以字符计，WER 以 Latin 词/CJK 字计；静音样本单独统计幻觉，不计入 CER/WER。
