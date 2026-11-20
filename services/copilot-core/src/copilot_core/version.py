"""包版本。"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version

try:
    __version__ = _distribution_version("copilot-core")
except PackageNotFoundError:  # pragma: no cover - 源码直接导入时才发生
    __version__ = "0.0.0"
