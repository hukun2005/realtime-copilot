"""命令行入口：``python -m copilot_core`` 或安装后的 ``copilot-core``。

退出码：

- ``0`` 正常退出
- ``2`` 配置非法（启动失败，不进入服务状态）
"""

from __future__ import annotations

import sys
from pathlib import Path

from .app import create_app
from .config import ConfigError, load_config, resolve_data_dir
from .logging_setup import configure_logging, get_logger
from .runtime import run_server
from .security import generate_token
from .version import __version__


def main(argv: list[str] | None = None) -> int:
    """启动 Core。

    Args:
        argv: 预留的参数列表；Phase 1 不接受命令行参数，所有配置来自文件与环境变量。

    Returns:
        进程退出码。
    """
    del argv
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    configure_logging(config.logging.level)
    logger = get_logger("copilot_core")

    data_dir = resolve_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    runtime_path: Path = data_dir / "runtime.json"

    app = create_app(
        config,
        token=generate_token(),
        data_dir=data_dir,
        version=__version__,
        runtime_path=runtime_path,
    )

    logger.info("starting core", extra={"version": __version__, "mode": config.mode})
    run_server(app, config)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
