# ASR CER/WER：aliyun

- 数据集：`realtime-copilot-p35-synthetic` v1
- 假设来源：aliyun
- 生成时间：2026-11-22T07:22:50.137961+00:00
- 缺失样本：0

## 汇总

| 指标 | 值 |
|---|---|
| CER micro | 12.54% |
| CER macro | 12.05% |
| WER micro | 10.53% |
| WER macro | 12.88% |
| 计分样本 | 10 / 11 |
| 静音幻觉 | 0 |

## 分类

| 类别 | 样本 | CER micro | CER macro |
|---|---|---|---|
| clean | 2 | 0.00% | 0.00% |
| filler | 1 | 0.00% | 0.00% |
| long | 1 | 0.00% | 0.00% |
| mixed | 1 | 14.29% | 14.29% |
| noisy | 3 | 17.65% | 13.42% |
| numbers | 1 | 48.00% | 48.00% |
| technical | 1 | 17.95% | 17.95% |

## 逐样本

| ID | 类别 | CER | WER | 错误/参考 | 幻觉 |
|---|---|---|---|---|---|
| clean_01 | clean | 0.00% | 0.00% | 0/23 | 否 |
| clean_02 | clean | 0.00% | 0.00% | 0/17 | 否 |
| technical_01 | technical | 17.95% | 23.53% | 7/39 | 否 |
| numbers_01 | numbers | 48.00% | 48.00% | 12/25 | 否 |
| mixed_01 | mixed | 14.29% | 11.76% | 3/21 | 否 |
| filler_01 | filler | 0.00% | 0.00% | 0/16 | 否 |
| long_01 | long | 0.00% | 0.00% | 0/69 | 否 |
| clean_01_snr15 | noisy | 0.00% | 0.00% | 0/23 | 否 |
| clean_01_snr5 | noisy | 4.35% | 4.35% | 1/23 | 否 |
| technical_01_snr10 | noisy | 35.90% | 41.18% | 14/39 | 否 |
| silence_01 | silence | 0.00% | 0.00% | 0/0 | 否 |

> CER 以字符计，WER 以 Latin 词/CJK 字计；静音样本单独统计幻觉，不计入 CER/WER。
