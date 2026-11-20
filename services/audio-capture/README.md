# audio-capture（C# 骨架）

Windows 音频进程的 Phase 1 骨架：**只做连接与协议**，不采集任何音频。

## 已实现

- 配置解析（命令行 > 环境变量 > `runtime.json` > `appsettings.json`）
- 连接 Core 的 WebSocket、发送 `hello`、等待 `system.ready`
- 应答 `ping`（回显 nonce）
- 识别断线、空闲超时、Core 主动关闭
- Ctrl+C 优雅关闭（发送 Close 帧并释放 socket）
- JSON Lines 日志（stdout）
- NAudio 设备枚举（`DeviceCatalog`，仅 Windows 且不打开音频流）
- **音频二进制帧**：`AudioFrameCodec` 序列化/校验（与 Python 错误码一致）、`SimulatedAudioSource` 生成确定性 20ms 帧、`AudioStreamer` 按帧率发送并支持 `--drop-every` 注入丢帧
- 模拟发送：`--simulate-audio --seconds=N --fps=50 --drop-every=500`

## 未实现（Phase 2-B）

WASAPI loopback / 进程级 loopback、麦克风、重采样、分帧、二进制帧上报、设备热插拔。
帧传输接口已预留在 `src/Capture/IAudioFrameSource.cs`。

## 运行

```bash
dotnet build services/audio-capture/AudioCapture.csproj -c Release
dotnet services/audio-capture/bin/Release/net10.0/audio-capture.dll
```

优先从 `runtime.json` 取地址与令牌；也可以显式指定：

```bash
dotnet ... --ws-url=ws://127.0.0.1:5000/ws/v1/events --token=<64 hex>
```

非回环地址、非 `ws://`、过短令牌都会在启动期被拒绝。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 正常退出 / Ctrl+C |
| 2 | 配置非法 |
| 3 | 无法连接 Core（拒绝、超时、鉴权失败） |
| 4 | 连接异常结束（断线、空闲超时） |

## 测试

```bash
dotnet test services/audio-capture/tests -c Release
```

测试用 Kestrel 起真实的 WebSocket 服务（不是 mock 对象），覆盖：握手成功、`system.error` 透出、`ping`→`pong`、服务端关闭、客户端取消、连接失败。
