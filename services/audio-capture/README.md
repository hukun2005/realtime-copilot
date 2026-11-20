# audio-capture

Windows 音频进程：连接 Core、采集音频（WASAPI）、把 20ms PCM16 帧以二进制消息发送出去。

## 已实现

- 配置解析（命令行 > 环境变量 > `runtime.json` > `appsettings.json`）
- 连接 Core 的 WebSocket、发送 `hello`、等待 `system.ready`
- 应答 `ping`（回显 nonce）
- 识别断线、空闲超时、Core 主动关闭
- Ctrl+C 优雅关闭（发送 Close 帧并释放 socket）
- JSON Lines 日志（stdout）
- 设备枚举：`--list-devices` 输出 JSON（`id` / `friendlyName` / `flow`）
- **音频二进制帧**：`AudioFrameCodec` 序列化/校验（与 Python 错误码一致）
- **模拟源**：`SimulatedAudioSource` + `AudioStreamer`，`--simulate-audio --seconds=N --fps=50 --drop-every=500`
- **真实采集**（Phase 2-B）：
  - `WasapiCaptureSource`：`WasapiRecorderBuilder` 打开设备，`WAVE_FORMAT_EXTENSIBLE` 归一化后进缓冲
  - `AudioFramer`：跨回调残留字节累积，按 20ms 切帧，时间戳按帧数推进
  - `FrameStreamer`：采集事件 → 有界队列 → 顺序发送；队列满丢最旧帧
  - 立体声 → 单声道，任意采样率 → 16 kHz（`WdlResamplingSampleProvider`）
  - 采集泵按 50fps 限速，避免把缓冲区积压瞬间灌给 Core
- 麦克风采集：`--capture-mic`（默认关闭）

## 未实现

- 进程级 loopback（按进程采集）——Phase 2-C
- 设备热插拔自动切换
- 静音事件（只有 Core 侧电平统计）

## 运行

```bash
dotnet build services/audio-capture/AudioCapture.csproj -c Release

# 只连接、不发音频
dotnet services/audio-capture/bin/Release/net10.0/audio-capture.dll

# 模拟音频（无需声卡，CI 使用）
dotnet ... --simulate-audio --seconds=60 --drop-every=500

# 真实采集：系统输出，可选麦克风
dotnet ... --capture-loopback --seconds=60
dotnet ... --capture-loopback --capture-mic --seconds=60

# 指定端点 / 列出端点
dotnet ... --capture-loopback --capture-device={0.0.0.00000000}.{guid}
dotnet ... --list-devices
```

优先从 `runtime.json` 取地址与令牌；也可以显式指定：

```bash
dotnet ... --ws-url=ws://127.0.0.1:5000/ws/v1/events --token=<64 hex>
```

非回环地址、非 `ws://`、过短令牌都会在启动期被拒绝。

## 已知环境差异

用 `WithDevice(默认端点)` 打开 loopback 时，本机曾出现"其他进程播放被采成静音"；现在默认交给 builder 解析端点，显式指定端点前会先修复枚举器生命周期（枚举器必须活到设备使用结束）。跨机器行为需在目标机上用 `scripts/capture-smoke.ps1` 复测。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 正常退出 / Ctrl+C / 模拟结束 |
| 2 | 配置非法，或平台不支持采集 |
| 3 | 无法连接 Core（拒绝、超时、鉴权失败） |
| 4 | 连接异常结束、发送失败或采集中断 |
