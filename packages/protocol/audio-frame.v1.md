# 音频二进制帧协议 v1

RealTime Copilot 的音频传输协议。音频以 **WebSocket Binary Message** 发送，一条消息恰好一帧，**不做 Base64、不进 JSON**。

- 状态：Phase 2-A 已实现（Python Core 接收、C# 发送、跨语言 fixtures）
- 适用连接：`hello.role = "audio"`（其他角色发送二进制帧会被拒绝，见 §8）
- 相关文档：[PLUGIN_API.md](../../PLUGIN_API.md)（JSON 事件协议）、[SECURITY.md](../../SECURITY.md)（鉴权与残余风险）

## 1. 默认音频格式

| 项 | 值 |
|---|---|
| sampleRate | 16000 Hz |
| channels | 1（mono） |
| sampleFormat | `1` = PCM signed 16-bit little-endian |
| frameDurationMs | 20 |
| payloadLength | 640 字节（16000 × 0.02 × 2） |
| 整帧长度 | **668 字节**（28 字节头 + 640 字节 PCM） |

20ms 是 VAD、ASR 与速率限制的共同时间粒度；当前版本**只接受**这一种组合，其他取值按无效帧丢弃（§7）。

## 2. 头部布局

所有多字节整数为 **little-endian**，头部固定 28 字节，紧接 PCM 负载。

| 偏移 | 长度 | 字段 | 类型 | 约束 |
|---:|---:|---|---|---|
| 0 | 1 | `protocolVersion` | uint8 | 必须为 `1` |
| 1 | 1 | `source` | uint8 | `0` = loopback（电脑输出），`1` = mic |
| 2 | 2 | `frameDurationMs` | uint16 | 必须为 `20` |
| 4 | 4 | `sequence` | uint32 | 每个 `source` 流独立计数，从任意值开始，逐帧 +1，允许回绕 |
| 8 | 8 | `captureTsNs` | uint64 | 采集侧单调时钟，单位纳秒；不是墙钟 |
| 16 | 4 | `sampleRate` | uint32 | 必须为 `16000` |
| 20 | 2 | `channels` | uint16 | 必须为 `1` |
| 22 | 2 | `sampleFormat` | uint16 | 必须为 `1`（PCM16LE） |
| 24 | 4 | `payloadLength` | uint32 | 必须为 `640`，且等于 `消息长度 - 28` |
| 28 | 640 | `payload` | bytes | PCM16LE 采样 |

布局等价结构（便于实现核对）：

```text
+--------+--------+--------+--------+--------+--------+--------+--------+
| ver(1) | src(1) | dur(2)          | sequence(4)                       |
+--------+--------+--------+--------+--------+--------+--------+--------+
| captureTsNs(8)                                                        |
+--------+--------+--------+--------+--------+--------+--------+--------+
| sampleRate(4)   | ch(2)  | fmt(2) | payloadLength(4)                  |
+--------+--------+--------+--------+--------+--------+--------+--------+
| PCM16LE payload (640 bytes)                                          |
+----------------------------------------------------------------------+
```

**没有 magic 字段**：WebSocket 的消息边界已提供分帧，二进制帧总是完整到达；字段级严格校验足以拒绝随机字节（首字节必须是 `0x01`，且长度、采样率、负载长度必须同时吻合）。

## 3. 字节序

- 多字节整数一律 little-endian。
- PCM 为 signed 16-bit little-endian。
- 不接受 big-endian 变体，也不做自动检测。

## 4. 长度限制

| 限制 | 值 | 行为 |
|---|---|---|
| 单帧最大长度 | 4096 字节（`audio.max_frame_bytes`） | 超限直接断开连接（1009），并计为 `too_large` |
| 头部长度 | 28 字节 | 不足则丢弃并计 `truncated_header` |
| 负载长度 | 必须 640 | 不符计 `payload_length_mismatch` / `invalid_payload_size` |
| 每秒帧数 | 200（`audio.max_frames_per_second`），突发 250 | 超限计 `rate_limited`，持续超限断开（1008） |

## 5. `sequence` 语义

- 作用域是单个 `source` 流：同一连接上 loopback 与 mic 各自计数。
- 发送方从任意值开始（推荐 0），每帧 +1，`uint32` 回绕（`0xFFFFFFFF → 0`）。
- 接收方判定（`last` 为上一帧，`delta = (seq - last) mod 2^32`）：
  - `delta == 1`：正常；
  - `1 < delta < 2^31`：**丢帧**，`gaps += delta - 1`，`gapEvents += 1`，接受当前帧；
  - `delta == 0` 或 `delta >= 2^31`：**重复/乱序**，`duplicates += 1`，丢弃当前帧。
- 丢帧只做统计，不重传：实时音频没有重传价值。

## 6. `captureTsNs` 语义

- 采集侧单调时钟的纳秒值（.NET `Stopwatch.GetTimestamp()` 换算，或 Python `time.perf_counter_ns()`）。
- 只用于计算帧间隔、抖动与端到端延迟，**不参与**跨进程排序。
- 接收方校验（每个 `source` 独立）：
  - 值为 `0`：`timestamp_invalid`，丢弃；
  - 比上一帧回退超过 1 秒：`timestamp_regression`，丢弃；
  - 单调递增或微小乱序（≤1 秒）：接受。

## 7. 版本兼容与解析失败

- `protocolVersion != 1` → `unsupported_version`，丢弃该帧。
- **单帧错误不关闭连接**：音频是实时流，丢一帧优于断开。错误只计数并限流记录日志。
- 例外（属于连接级违规，直接关闭）：
  - 非 `audio` 角色发送二进制帧 → `system.error{code:"invalid_role"}` + 1008；
  - 单帧超过 `max_frame_bytes` → 1009；
  - 持续触发速率限制（1 秒内超过 10 次）→ 1008；
  - 1 秒内无效帧超过 25 帧 → 1008（防止用垃圾帧打满日志）。
- 未来的 `v2` 需要新增字段或改变语义时，客户端与服务端各自声明版本；服务端对未知版本按 `unsupported_version` 丢弃，而不是猜测。

## 8. 角色与安全

- 只有 `role=audio` 的连接可以发送二进制帧；`ui` / `bridge` / `tests` 发送会被拒绝并断开。
- `role=audio` 不能发送 `capture.start` / `capture.stop`（那是 UI/编排侧的命令）；发送计为 `invalid_role`。
- 音频帧不进入 JSON 事件总线，不写日志内容，不落盘；Core 只在内存中统计（帧数、字节数、丢帧、重复、无效、最后时间戳）。
- 服务端不校验 PCM 语义（静音、削波），Phase 2-B 由采集侧负责。

## 9. 跨语言 fixtures

`packages/protocol/fixtures/audio/` 下的二进制样本被 Python 与 C# 测试共同读取：

| 文件 | 内容 | 期望结果 |
|---|---|---|
| `valid-frame.bin` | v1、loopback、seq=1、ts=1000000000、640 字节渐变 PCM | 解析成功，字段与元数据一致 |
| `invalid-version.bin` | `protocolVersion=2` | `unsupported_version` |
| `invalid-rate.bin` | `sampleRate=44100` | `invalid_sample_rate` |
| `invalid-channels.bin` | `channels=2` | `invalid_channels` |
| `invalid-format.bin` | `sampleFormat=2` | `invalid_sample_format` |
| `truncated-header.bin` | 只有 20 字节 | `truncated_header` |
| `payload-mismatch.bin` | 头部声明 640，实际 100 字节 | `payload_length_mismatch` |

`valid-frame.meta.json` 记录该样本的期望字段值，避免测试里硬编码偏移。

## 10. 参考实现

| 语言 | 位置 |
|---|---|
| Python 解析/编码 | `services/copilot-core/src/copilot_core/audio/frame.py` |
| C# 序列化/解析 | `services/audio-capture/src/Audio/AudioFrameCodec.cs` |
| 会话统计 | `services/copilot-core/src/copilot_core/audio/session.py` |
