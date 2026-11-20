"""令牌、文件加固与运行时文件。"""

from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from copilot_core.security import (
    RuntimeInfo,
    build_runtime_info,
    generate_token,
    remove_runtime_file,
    restrict_to_owner,
    tokens_equal,
    write_runtime_file,
)


def test_token_is_256_bit_hex_and_unique() -> None:
    tokens = {generate_token() for _ in range(200)}
    assert len(tokens) == 200
    assert all(len(token) == 64 for token in tokens)
    assert all(all(character in "0123456789abcdef" for character in token) for token in tokens)


def test_tokens_equal_is_exact() -> None:
    token = generate_token()
    assert tokens_equal(token, token)
    assert not tokens_equal(token, token + "0")
    assert not tokens_equal(token, token.upper())


def test_write_runtime_file_is_atomic_and_readable(tmp_path: Path) -> None:
    path = tmp_path / "runtime.json"
    info = build_runtime_info(
        version="0.1.0",
        protocol=1,
        host="127.0.0.1",
        port=45678,
        token="a" * 64,
        mode="standalone",
        data_dir=tmp_path,
    )
    hardened = write_runtime_file(path, info)

    assert path.exists()
    assert not (tmp_path / "runtime.json.tmp").exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["wsUrl"] == "ws://127.0.0.1:45678/ws/v1/events"
    assert payload["token"] == "a" * 64
    assert payload["protocol"] == 1
    assert isinstance(hardened, bool)


def test_runtime_file_replaces_previous(tmp_path: Path) -> None:
    path = tmp_path / "runtime.json"
    first = build_runtime_info(
        version="0.1.0", protocol=1, host="127.0.0.1", port=1, token="a" * 64, mode="standalone", data_dir=tmp_path
    )
    second = build_runtime_info(
        version="0.1.0", protocol=1, host="127.0.0.1", port=2, token="b" * 64, mode="harness", data_dir=tmp_path
    )
    write_runtime_file(path, first)
    write_runtime_file(path, second)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["port"] == 2
    assert payload["mode"] == "harness"


def test_remove_runtime_file_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "runtime.json"
    path.write_text("{}", encoding="utf-8")
    remove_runtime_file(path)
    assert not path.exists()
    remove_runtime_file(path)


def test_restrict_to_owner_returns_bool(tmp_path: Path) -> None:
    path = tmp_path / "secret.json"
    path.write_text("{}", encoding="utf-8")
    assert isinstance(restrict_to_owner(path), bool)


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 没有 POSIX mode 语义")
def test_posix_mode_is_0600(tmp_path: Path) -> None:
    path = tmp_path / "secret.json"
    path.write_text("{}", encoding="utf-8")
    assert restrict_to_owner(path) is True
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600


def test_runtime_info_is_immutable() -> None:
    info = RuntimeInfo(
        version="0.1.0",
        protocol=1,
        pid=1,
        host="127.0.0.1",
        port=1,
        ws_url="ws://127.0.0.1:1/ws/v1/events",
        token="t",
        mode="standalone",
        started_at="2026-01-01T00:00:00+00:00",
        data_dir="/tmp",
    )
    with pytest.raises(FrozenInstanceError):
        info.port = 2  # type: ignore[misc]
