"""PCM 电平计算：保留原有导入路径，实现位于 :mod:`copilot_core.pcm_level`。

不保留采样，只返回 dBFS；静音返回下限 :data:`SILENCE_DBFS`，保证 JSON 可序列化。
"""

from __future__ import annotations

from ..pcm_level import SILENCE_DBFS, peak_dbfs, rms_dbfs

__all__ = ["SILENCE_DBFS", "peak_dbfs", "rms_dbfs"]
