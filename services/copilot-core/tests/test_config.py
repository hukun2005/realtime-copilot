"""配置加载与校验。"""

from __future__ import annotations

from pathlib import Path

import pytest
from copilot_core.config import (
    AppConfig,
    default_config_path,
    load_config,
    resolve_data_dir,
)
from copilot_core.errors import ConfigError


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_are_loopback_only(tmp_path: Path) -> None:
    config = load_config(env={}, data_dir=tmp_path)
    assert config.server.host == "127.0.0.1"
    assert config.server.port == 0
    assert config.server.auth_timeout_ms == 2000
    assert config.mode == "standalone"
    assert config.capture.frame_ms == 20


def test_toml_file_is_loaded(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
mode = "harness"

[server]
port = 1234
auth_timeout_ms = 900

[logging]
level = "debug"
""",
    )
    config = load_config(path, env={}, data_dir=tmp_path)
    assert config.mode == "harness"
    assert config.server.port == 1234
    assert config.server.auth_timeout_ms == 900
    assert config.logging.level == "debug"


def test_env_overrides_file(tmp_path: Path) -> None:
    path = write_config(tmp_path, '[server]\nport = 1234\n')
    env = {"RTC_SERVER_PORT": "4321", "RTC_CAPTURE_MIC_ENABLED": "true", "RTC_MODE": "harness"}
    config = load_config(path, env=env, data_dir=tmp_path)
    assert config.server.port == 4321
    assert config.capture.mic_enabled is True
    assert config.mode == "harness"


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.10"])
def test_non_loopback_host_fails_loudly(tmp_path: Path, host: str) -> None:
    path = write_config(tmp_path, f'[server]\nhost = "{host}"\n')
    with pytest.raises(ConfigError) as excinfo:
        load_config(path, env={}, data_dir=tmp_path)
    assert excinfo.value.path == "server.host"


def test_invalid_port_fails(tmp_path: Path) -> None:
    path = write_config(tmp_path, "[server]\nport = 70000\n")
    with pytest.raises(ConfigError) as excinfo:
        load_config(path, env={}, data_dir=tmp_path)
    assert excinfo.value.path == "server.port"


def test_unknown_field_fails(tmp_path: Path) -> None:
    path = write_config(tmp_path, '[server]\nportt = 1234\n')
    with pytest.raises(ConfigError):
        load_config(path, env={}, data_dir=tmp_path)


def test_invalid_env_value_fails(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_config(env={"RTC_SERVER_PORT": "abc"}, data_dir=tmp_path)
    assert excinfo.value.path == "server.port"

    with pytest.raises(ConfigError):
        load_config(env={"RTC_CAPTURE_MIC_ENABLED": "maybe"}, data_dir=tmp_path)


def test_missing_file_uses_defaults(tmp_path: Path) -> None:
    config = load_config(tmp_path / "absent.toml", env={}, data_dir=tmp_path)
    assert isinstance(config, AppConfig)
    assert config.server.host == "127.0.0.1"


def test_data_dir_precedence(tmp_path: Path) -> None:
    override = tmp_path / "override"
    assert resolve_data_dir({"RTC_DATA_DIR": str(override)}) == override.resolve()

    windows = resolve_data_dir({"LOCALAPPDATA": str(tmp_path / "local")})
    if windows.drive:
        assert windows.name == "RealTimeCopilot"


def test_database_path_defaults_to_data_dir(tmp_path: Path) -> None:
    config = AppConfig()
    assert config.database_path(tmp_path) == (tmp_path / "copilot.db").resolve()

    explicit = tmp_path / "custom.db"
    configured = AppConfig.model_validate({"storage": {"path": str(explicit)}})
    assert configured.database_path(tmp_path) == explicit.resolve()


def test_default_config_path_is_under_data_dir(tmp_path: Path) -> None:
    assert default_config_path(tmp_path) == tmp_path / "config.toml"
