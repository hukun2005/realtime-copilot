"""PCM 电平计算：用于采集健康度与"是否真的采到声音"的判定。

不保留采样，只返回 dBFS；静音返回下限 :data:`SILENCE_DBFS`，保证 JSON 可序列化。
"""

from __future__ import annotations

import math
import struct
from typing import Final

#: 静音与极小信号的下限，避免 ``-inf`` 进入 JSON。
SILENCE_DBFS: Final = -120.0

_FULL_SCALE = 32768.0


def _samples(pcm: bytes) -> tuple[int, ...]:
    count = len(pcm) // 2
    if count == 0:
        return ()
    return struct.unpack(f"<{count}h", pcm[: count * 2])


def peak_dbfs(pcm: bytes) -> float:
    """峰值电平。

    Args:
        pcm: PCM16LE 字节。

    Returns:
        dBFS；空输入或全零返回 :data:`SILENCE_DBFS`。
    """
    values = _samples(pcm)
    if not values:
        return SILENCE_DBFS
    peak = max(max(values), -min(values))
    if peak <= 0:
        return SILENCE_DBFS
    return round(20.0 * math.log10(peak / _FULL_SCALE), 1)


def rms_dbfs(pcm: bytes) -> float:
    """均方根电平。

    Args:
        pcm: PCM16LE 字节。

    Returns:
        dBFS；空输入或全零返回 :data:`SILENCE_DBFS`。
    """
    values = _samples(pcm)
    if not values:
        return SILENCE_DBFS
    mean_square = sum(value * value for value in values) / len(values)
    if mean_square <= 0:
        return SILENCE_DBFS
    return round(10.0 * math.log10(mean_square / (_FULL_SCALE * _FULL_SCALE)), 1)
