"""令牌、鉴权比较与本地文件加固。

关于 Windows 的说明（务必与 SECURITY.md 一起读）：

- POSIX 上 ``chmod 0600`` 是真实强制。
- Windows 没有 POSIX mode 语义，``os.chmod`` 只影响只读位。因此这里在 Windows 上
  改用 ``icacls`` 移除继承并只授予当前用户访问，失败时降级为"依赖上级目录的
  用户级 ACL"并记录原因——数据目录位于 ``%LOCALAPPDATA%``，默认已按用户隔离。
- 令牌本身是**本地能力凭证**，不是 API Key：它的作用是阻止同机其他用户进程连接
  Core。API Key 不进入 Core，Phase 5 起由 Electron 的 ``safeStorage``（DPAPI）持有
  并通过已鉴权的通道下发。
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TOKEN_BYTES = 32


def generate_token() -> str:
    """生成 64 字符的十六进制令牌（256 位随机）。"""
    return secrets.token_hex(TOKEN_BYTES)


def tokens_equal(expected: str, received: str) -> bool:
    """常量时间比较，避免计时侧信道。

    Args:
        expected: 服务端持有的令牌。
        received: 客户端提交的令牌。
    """
    return hmac.compare_digest(expected.encode("utf-8"), received.encode("utf-8"))


def restrict_to_owner(path: Path) -> bool:
    """尽力把文件收敛为当前用户可访问。

    Args:
        path: 已存在的文件。

    Returns:
        是否成功施加了平台级加固。``False`` 表示退化为上级目录 ACL，
        调用方应记录 warning。
    """
    try:
        if sys.platform == "win32":
            return _restrict_windows(path)
        os.chmod(path, 0o600)
        return True
    except OSError:
        return False


def _restrict_windows(path: Path) -> bool:
    """用 icacls 移除继承 ACE，只授予当前用户完全控制。"""
    user = os.environ.get("USERNAME")
    if not user:
        return False
    steps = (
        ["icacls", str(path), "/inheritance:r"],
        ["icacls", str(path), "/grant:r", f"{user}:F"],
    )
    for command in steps:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            return False
    return True


@dataclass(frozen=True)
class RuntimeInfo:
    """写入 ``runtime.json`` 的运行时握手信息。"""

    version: str
    protocol: int
    pid: int
    host: str
    port: int
    ws_url: str
    token: str
    mode: str
    started_at: str
    data_dir: str

    def to_json(self) -> dict[str, Any]:
        """转换为线格式字典（camelCase，与协议命名一致）。"""
        return {
            "version": self.version,
            "protocol": self.protocol,
            "pid": self.pid,
            "host": self.host,
            "port": self.port,
            "wsUrl": self.ws_url,
            "token": self.token,
            "mode": self.mode,
            "startedAt": self.started_at,
            "dataDir": self.data_dir,
        }


def build_runtime_info(
    *,
    version: str,
    protocol: int,
    host: str,
    port: int,
    token: str,
    mode: str,
    data_dir: Path,
) -> RuntimeInfo:
    """构造运行信息。

    Args:
        version: Core 版本。
        protocol: 协议版本。
        host: 监听地址（始终为 127.0.0.1）。
        port: 实际监听端口。
        token: 本次运行的令牌。
        mode: ``standalone`` 或 ``harness``。
        data_dir: 数据目录。
    """
    return RuntimeInfo(
        version=version,
        protocol=protocol,
        pid=os.getpid(),
        host=host,
        port=port,
        ws_url=f"ws://{host}:{port}/ws/v1/events",
        token=token,
        mode=mode,
        started_at=datetime.now(tz=UTC).isoformat(timespec="seconds"),
        data_dir=str(data_dir),
    )


def write_runtime_file(path: Path, info: RuntimeInfo) -> bool:
    """原子写入 ``runtime.json`` 并尽力加固。

    Args:
        path: 目标路径。
        info: 运行信息。

    Returns:
        是否成功施加平台级文件加固。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(info.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
    return restrict_to_owner(path)


def remove_runtime_file(path: Path) -> None:
    """删除 ``runtime.json``；不存在时静默返回。"""
    try:
        path.unlink()
    except FileNotFoundError:
        return
