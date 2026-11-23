"""协议 v1 的 Python 契约测试：fixtures、往返、错误分类与信封一致性。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from copilot_protocol import (
    EVENT_TYPES,
    PROTOCOL_VERSION,
    ProtocolViolation,
    dumps,
    event_to_wire,
    make_ping,
    make_pong,
    make_system_error,
    make_system_ready,
    new_event_id,
    new_request_id,
    parse_event,
)

PROTOCOL_DIR = Path(__file__).resolve().parents[2]
FIXTURES = PROTOCOL_DIR / "fixtures"
SCHEMA = json.loads((PROTOCOL_DIR / "schema" / "events.v1.json").read_text(encoding="utf-8"))

VALID_FIXTURES = sorted(path for path in FIXTURES.glob("*.json"))
INVALID_FIXTURES = sorted((FIXTURES / "invalid").glob("*.json"))


def strip_nulls(value: Any) -> Any:
    """递归删除值为 None 的键，用于跨语言比较（可选字段允许缺省）。"""
    if isinstance(value, dict):
        return {key: strip_nulls(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [strip_nulls(item) for item in value]
    return value


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def test_fixture_coverage() -> None:
    assert len(VALID_FIXTURES) >= len(EVENT_TYPES)
    names = {path.stem for path in VALID_FIXTURES}
    assert names == set(EVENT_TYPES)


@pytest.mark.parametrize("path", VALID_FIXTURES, ids=lambda p: p.name)
def test_valid_fixture_parses(path: Path) -> None:
    event = parse_event(path.read_text(encoding="utf-8"))
    assert event.type == path.stem
    assert event.v == PROTOCOL_VERSION


@pytest.mark.parametrize("path", VALID_FIXTURES, ids=lambda p: p.name)
def test_roundtrip_preserves_fields(path: Path) -> None:
    original = load(path)
    wire = event_to_wire(parse_event(original))
    # 再次解析证明序列化结果仍是合法事件
    reparsed = event_to_wire(parse_event(wire))
    assert strip_nulls(wire) == strip_nulls(original)
    assert reparsed == wire


def test_unknown_event_type_is_unsupported() -> None:
    with pytest.raises(ProtocolViolation) as excinfo:
        parse_event(load(FIXTURES / "invalid" / "unknown-type.json"))
    assert excinfo.value.code == "unsupported_event"


def test_bad_version_is_reported_separately() -> None:
    with pytest.raises(ProtocolViolation) as excinfo:
        parse_event(load(FIXTURES / "invalid" / "bad-version.json"))
    assert excinfo.value.code == "protocol_version_unsupported"


@pytest.mark.parametrize(
    "fixture",
    ["missing-payload.json", "extra-envelope-field.json", "wrong-role.json"],
)
def test_invalid_fixtures_are_rejected(fixture: str) -> None:
    with pytest.raises(ProtocolViolation) as excinfo:
        parse_event(load(FIXTURES / "invalid" / fixture))
    assert excinfo.value.code in {"invalid_payload", "invalid_envelope"}


def test_non_object_and_malformed_json_rejected() -> None:
    with pytest.raises(ProtocolViolation) as excinfo:
        parse_event("[1,2,3]")
    assert excinfo.value.code == "invalid_envelope"

    with pytest.raises(ProtocolViolation) as excinfo:
        parse_event("{not json")
    assert excinfo.value.code == "invalid_envelope"


def test_envelope_is_uniform_across_events() -> None:
    defs = {name: spec for name, spec in SCHEMA["$defs"].items() if name.endswith("Event")}
    assert len(defs) == len(EVENT_TYPES)
    answer_types = {"answer.started", "answer.delta", "answer.completed", "answer.cancelled", "answer.failed"}
    for name, spec in defs.items():
        event_type = spec["properties"]["type"].get("const", "")
        if event_type in answer_types:
            assert spec["required"] == [
                "v", "type", "eventId", "requestId", "sessionId",
                "questionId", "generation", "sequence", "ts", "payload",
            ], name
        else:
            assert spec["required"] == ["v", "type", "eventId", "ts", "payload"], name
        assert spec["properties"]["v"] == {"const": PROTOCOL_VERSION}, name
        assert spec["additionalProperties"] is False, name


def test_oneof_order_matches_event_types() -> None:
    referenced = []
    for entry in SCHEMA["oneOf"]:
        name = entry["$ref"].rsplit("/", 1)[-1]
        referenced.append(SCHEMA["$defs"][name]["properties"]["type"]["const"])
    assert referenced == list(EVENT_TYPES)


def test_ids_match_pattern_and_are_unique() -> None:
    import re

    ids = {new_event_id() for _ in range(500)}
    assert len(ids) == 500
    assert all(re.fullmatch(r"evt_[A-Za-z0-9_-]{8,64}", value) for value in ids)
    assert re.fullmatch(r"req_[A-Za-z0-9_-]{8,64}", new_request_id())


def test_constructors_produce_parseable_events() -> None:
    ready = make_system_ready(server_version="0.1.0", capabilities=["capture"])
    error = make_system_error(code="auth_failed", message="invalid token", retryable=False)
    for event in (ready, error, make_ping(), make_pong("hb-1")):
        wire = event_to_wire(event)
        parsed = parse_event(wire)
        assert parsed.type == event.type
        assert dumps(parsed) == dumps(event)
