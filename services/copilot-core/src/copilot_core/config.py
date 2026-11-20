"""配置加载：TOML 文件 + ``RTC_*`` 环境变量覆盖 + 启动期强校验。

设计约束：

- 监听地址只允许 ``127.0.0.1``，由 ``Literal`` 在解析期强制；任何其他值都会让进程启动失败。
- 未知字段一律拒绝（``extra="forbid"``），避免拼写错误被静默忽略。
- 环境变量只覆盖已知键，且做类型转换；非法值同样启动失败。
"""

from __future__ import annotations

import json
import os
import shlex
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .errors import ConfigError

APP_NAME = "RealTimeCopilot"

#: 环境变量到配置路径的映射；值是解析器名。
ENV_OVERRIDES: dict[str, tuple[tuple[str, ...], str]] = {
    "RTC_MODE": (("mode",), "str"),
    "RTC_SERVER_PORT": (("server", "port"), "int"),
    "RTC_SERVER_AUTH_TIMEOUT_MS": (("server", "auth_timeout_ms"), "int"),
    "RTC_SERVER_HEARTBEAT_INTERVAL_MS": (("server", "heartbeat_interval_ms"), "int"),
    "RTC_SERVER_HEARTBEAT_TIMEOUT_MS": (("server", "heartbeat_timeout_ms"), "int"),
    "RTC_LOG_LEVEL": (("logging", "level"), "str"),
    "RTC_STORAGE_PATH": (("storage", "path"), "str"),
    "RTC_CAPTURE_FRAME_MS": (("capture", "frame_ms"), "int"),
    "RTC_CAPTURE_MIC_ENABLED": (("capture", "mic_enabled"), "bool"),
    "RTC_CAPTURE_LOOPBACK_ENABLED": (("capture", "loopback_enabled"), "bool"),
    "RTC_CAPTURE_SIDECAR": (("capture", "sidecar_command"), "command"),
    "RTC_CAPTURE_START_TIMEOUT_MS": (("capture", "start_timeout_ms"), "int"),
    "RTC_CAPTURE_STOP_TIMEOUT_MS": (("capture", "stop_timeout_ms"), "int"),
    "RTC_CAPTURE_TICKET_TTL_SECONDS": (("capture", "ticket_ttl_seconds"), "int"),
}


class ServerConfig(BaseModel):
    """监听、握手与心跳参数。"""

    model_config = ConfigDict(extra="forbid")

    #: 只允许回环地址；这是安全不变量，不是可调参数。
    host: Literal["127.0.0.1"] = "127.0.0.1"
    port: int = Field(default=0, ge=0, le=65535)
    auth_timeout_ms: int = Field(default=2000, ge=100, le=30000)
    heartbeat_interval_ms: int = Field(default=15000, ge=100, le=120000)
    heartbeat_timeout_ms: int = Field(default=30000, ge=200, le=300000)
    max_message_bytes: int = Field(default=262144, ge=4096, le=4 * 1024 * 1024)


class CaptureConfig(BaseModel):
    """采集控制参数：sidecar 启动命令、超时与票据有效期。"""

    model_config = ConfigDict(extra="forbid")

    frame_ms: Literal[10, 20, 30, 40] = 20
    sample_rate: Literal[16000] = 16000
    loopback_enabled: bool = True
    mic_enabled: bool = False
    #: sidecar 启动命令（argv 列表）；为空表示禁用控制面（``capture.start`` 返回 sidecar_unavailable）。
    sidecar_command: list[str] = Field(default_factory=list)
    #: 发出 capture.start 后等待 sidecar 报告 capturing 的上限。
    start_timeout_ms: int = Field(default=8000, ge=1000, le=60_000)
    #: 发出 capture.stop 后等待 sidecar 报告 idle 的上限；超时强制结束进程。
    stop_timeout_ms: int = Field(default=5000, ge=1000, le=60_000)
    #: 音频票据有效期。
    ticket_ttl_seconds: int = Field(default=60, ge=5, le=600)


class LoggingConfig(BaseModel):
    """结构化日志参数。"""

    model_config = ConfigDict(extra="forbid")

    level: Literal["debug", "info", "warning", "error"] = "info"


class StorageConfig(BaseModel):
    """SQLite 位置；缺省为数据目录下的 copilot.db。"""

    model_config = ConfigDict(extra="forbid")

    path: str | None = None


class AudioConfig(BaseModel):
    """音频二进制帧的接收限制（见 ``packages/protocol/audio-frame.v1.md``）。"""

    model_config = ConfigDict(extra="forbid")

    #: 单帧上限；最小 668（28 字节头 + 640 字节 PCM）。
    max_frame_bytes: int = Field(default=4096, ge=668, le=65_536)
    #: 每秒允许的帧数；19 帧/秒足以覆盖 20ms 帧的 3 倍抖动余量。
    max_frames_per_second: int = Field(default=200, ge=20, le=2000)
    #: 令牌桶突发容量。
    burst_frames: int = Field(default=250, ge=20, le=5000)
    #: 1 秒窗口内允许的无效帧数，超过即断开。
    max_invalid_per_second: int = Field(default=25, ge=1, le=1000)
    #: 1 秒窗口内允许的超速次数，超过即断开。
    max_rate_violations_per_second: int = Field(default=10, ge=1, le=1000)


class AppConfig(BaseModel):
    """完整配置。"""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["standalone", "harness"] = "standalone"
    server: ServerConfig = Field(default_factory=ServerConfig)
    capture: CaptureConfig = Field(default_factory=CaptureConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)

    def database_path(self, data_dir: Path) -> Path:
        """返回 SQLite 绝对路径。

        Args:
            data_dir: 当前数据目录，仅在未显式配置时使用。
        """
        if self.storage.path:
            return Path(self.storage.path).expanduser().resolve()
        return (data_dir / "copilot.db").resolve()


def resolve_data_dir(env: Mapping[str, str] | None = None) -> Path:
    """解析数据目录。

    优先级：``RTC_DATA_DIR`` > Windows ``%LOCALAPPDATA%`` / 其他平台 ``$XDG_DATA_HOME``。

    Args:
        env: 环境变量映射，默认取 ``os.environ``。
    """
    values = os.environ if env is None else env
    override = values.get("RTC_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        base = values.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
        return (root / APP_NAME).resolve()
    xdg = values.get("XDG_DATA_HOME")
    root = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return (root / "realtime-copilot").resolve()


def default_config_path(data_dir: Path) -> Path:
    """返回默认配置文件路径。"""
    return data_dir / "config.toml"


def load_config(
    path: Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
    data_dir: Path | None = None,
) -> AppConfig:
    """加载并校验配置。

    Args:
        path: 显式配置文件路径；缺省用 ``RTC_CONFIG_PATH`` 或数据目录下的 ``config.toml``。
        env: 环境变量映射，默认取 ``os.environ``。
        data_dir: 数据目录覆盖，主要用于测试。

    Returns:
        校验后的 :class:`AppConfig`。

    Raises:
        ConfigError: 文件不可读、TOML 非法、字段非法或环境变量非法。
    """
    values = os.environ if env is None else env
    root = data_dir or resolve_data_dir(values)

    config_path = path
    if config_path is None:
        env_path = values.get("RTC_CONFIG_PATH")
        config_path = Path(env_path).expanduser() if env_path else default_config_path(root)

    raw: dict[str, Any] = {}
    if config_path.exists():
        try:
            raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:  # pragma: no cover - 依赖具体消息
            raise ConfigError(f"invalid TOML: {exc}", path=str(config_path)) from exc
        except OSError as exc:
            raise ConfigError(f"cannot read config: {exc}", path=str(config_path)) from exc

    _apply_env(raw, values)

    try:
        return AppConfig.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first.get("loc", ())) or "<root>"
        raise ConfigError(str(first.get("msg")), path=location) from exc


def _apply_env(raw: dict[str, Any], env: Mapping[str, str]) -> None:
    """把 ``RTC_*`` 覆盖写入原始配置字典。"""
    for name, (path, kind) in ENV_OVERRIDES.items():
        if name not in env:
            continue
        value: Any = env[name]
        try:
            if kind == "int":
                value = int(value)
            elif kind == "bool":
                value = _parse_bool(value)
            elif kind == "command":
                text = value.strip()
                # Windows 路径含反斜杠：JSON 数组原样解析，否则用非 POSIX 拆分（避免吞掉 \）。
                value = json.loads(text) if text.startswith("[") else shlex.split(text, posix=False)
        except ValueError as exc:
            raise ConfigError(f"{name} must be {kind}: {value!r}", path=".".join(path)) from exc
        target = raw
        for key in path[:-1]:
            child = target.get(key)
            if not isinstance(child, dict):
                child = {}
                target[key] = child
            target = child
        target[path[-1]] = value


def _parse_bool(value: str) -> bool:
    """解析布尔环境变量，接受常见拼写。"""
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(value)
