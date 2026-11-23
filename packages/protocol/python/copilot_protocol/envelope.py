"""协议 v1 的 Python 侧运行时：解析、分类、构造与序列化。

生成的模型在 :mod:`copilot_protocol.models`；本模块只补充手写逻辑，
与 TypeScript 侧 ``packages/protocol/src/index.ts`` 行为一一对应。
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, TypeAlias

from pydantic import BaseModel, ValidationError

from .models import (
    AnswerCancelledEvent,
    AnswerCompletedEvent,
    AnswerDeltaEvent,
    AnswerFailedEvent,
    AnswerStartedEvent,
    AnswerSubscribeEvent,
    AnswerUnsubscribeEvent,
    AsrErrorEvent,
    AsrFinalEvent,
    AsrPartialEvent,
    AsrStatusEvent,
    AudioStatusEvent,
    AudioStatusPayload,
    CaptureStartEvent,
    CaptureStopEvent,
    ErrorCode,
    EventV1,
    HeartbeatPayload,
    HelloEvent,
    PingEvent,
    PongEvent,
    SystemErrorEvent,
    SystemErrorPayload,
    SystemReadyEvent,
    SystemReadyPayload,
)

PROTOCOL_VERSION = 1

EVENT_TYPES: tuple[str, ...] = (
    "hello",
    "system.ready",
    "system.error",
    "ping",
    "pong",
    "audio.status",
    "capture.start",
    "capture.stop",
    "asr.status",
    "asr.partial",
    "asr.final",
    "asr.error",
    "transcript.segment",
    "transcript.reset",
    "answer.started",
    "answer.delta",
    "answer.completed",
    "answer.cancelled",
    "answer.failed",
    "answer.subscribe",
    "answer.unsubscribe",
)

ConcreteEvent: TypeAlias = (
    HelloEvent
    | SystemReadyEvent
    | SystemErrorEvent
    | PingEvent
    | PongEvent
    | AudioStatusEvent
    | CaptureStartEvent
    | CaptureStopEvent
    | AnswerStartedEvent
    | AnswerDeltaEvent
    | AnswerCompletedEvent
    | AnswerCancelledEvent
    | AnswerFailedEvent
    | AnswerSubscribeEvent
    | AnswerUnsubscribeEvent
)


class ProtocolViolation(Exception):
    """一条消息不符合协议时抛出；``code`` 与 schema 的 ErrorCode 一致。"""

    def __init__(self, code: ErrorCode, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def now_ms() -> int:
    """当前 Unix 毫秒时间戳。"""
    return int(time.time() * 1000)


def new_event_id() -> str:
    """生成事件 ID，满足 ``^evt_[A-Za-z0-9_-]{8,64}$``。"""
    return f"evt_{uuid.uuid4().hex}"


def new_request_id() -> str:
    """生成请求关联 ID，满足 ``^req_[A-Za-z0-9_-]{8,64}$``。"""
    return f"req_{uuid.uuid4().hex}"


def parse_event(raw: str | bytes | dict[str, Any]) -> ConcreteEvent:
    """解析并校验一条消息。

    Args:
        raw: JSON 文本、UTF-8 字节或已解析的字典。

    Returns:
        具体事件模型（``EventV1`` 联合成员）。

    Raises:
        ProtocolViolation: 版本不兼容、信封非法、未知事件或 payload 不合法。
    """
    data = _load(raw)
    if not isinstance(data, dict):
        raise ProtocolViolation("invalid_envelope", "event must be a JSON object")
    if data.get("v") != PROTOCOL_VERSION:
        raise ProtocolViolation(
            "protocol_version_unsupported",
            f"unsupported protocol version: {data.get('v')!r}",
        )
    event_type = data.get("type")
    if not isinstance(event_type, str):
        raise ProtocolViolation("invalid_envelope", "type must be a string")
    if event_type not in EVENT_TYPES:
        raise ProtocolViolation("unsupported_event", f"unknown event type: {event_type}")
    try:
        return EventV1.model_validate(data).root
    except ValidationError as exc:
        first = exc.errors()[0]
        location = "/".join(str(part) for part in first.get("loc", ()))
        raise ProtocolViolation("invalid_payload", f"{location or '/'}: {first.get('msg')}") from exc


def event_to_wire(event: BaseModel) -> dict[str, Any]:
    """把事件模型序列化为可发送的字典。

    省略值为 ``None`` 的可选字段：信封的 ``requestId`` 只允许字符串，
    payload 的可空字段在 schema 中均非必需。
    """
    return event.model_dump(by_alias=True, mode="json", exclude_none=True)


def dumps(event: BaseModel) -> str:
    """序列化为紧凑 JSON 文本。"""
    return json.dumps(event_to_wire(event), ensure_ascii=False, separators=(",", ":"))


def make_ping(nonce: str | None = None) -> PingEvent:
    """构造 ping 事件。"""
    return PingEvent(
        v=PROTOCOL_VERSION,
        type="ping",
        event_id=new_event_id(),
        ts=now_ms(),
        payload=HeartbeatPayload(nonce=nonce),
    )


def make_pong(nonce: str | None = None) -> PongEvent:
    """构造 pong 事件。"""
    return PongEvent(
        v=PROTOCOL_VERSION,
        type="pong",
        event_id=new_event_id(),
        ts=now_ms(),
        payload=HeartbeatPayload(nonce=nonce),
    )


def make_system_ready(
    *,
    server_version: str,
    capabilities: list[str],
    session_id: str | None = None,
    request_id: str | None = None,
) -> SystemReadyEvent:
    """构造 system.ready 事件。"""
    return SystemReadyEvent(
        v=PROTOCOL_VERSION,
        type="system.ready",
        event_id=new_event_id(),
        request_id=request_id,
        ts=now_ms(),
        payload=SystemReadyPayload(
            server_version=server_version,
            protocol=PROTOCOL_VERSION,
            capabilities=capabilities,
            session_id=session_id,
        ),
    )


def make_system_error(
    *,
    code: ErrorCode,
    message: str,
    retryable: bool,
    request_id: str | None = None,
) -> SystemErrorEvent:
    """构造 system.error 事件。"""
    return SystemErrorEvent(
        v=PROTOCOL_VERSION,
        type="system.error",
        event_id=new_event_id(),
        request_id=request_id,
        ts=now_ms(),
        payload=SystemErrorPayload(code=code, message=message, retryable=retryable),
    )


def _load(raw: str | bytes | dict[str, Any]) -> Any:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProtocolViolation("invalid_envelope", f"invalid JSON: {exc.msg}") from exc


def make_audio_status(
    *,
    state: str,
    since: int,
    source: str | None = None,
    device_id: str | None = None,
    mode: str | None = None,
    error: str | None = None,
    error_code: str | None = None,
    sources: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> AudioStatusEvent:
    """构造 audio.status 事件。

    Args:
        state: ``idle`` / ``starting`` / ``capturing`` / ``stopping`` / ``error``。
        since: 进入该状态的时间戳（毫秒，单调或墙钟由生产者决定）。
        source: ``loopback`` / ``mic`` / None。
        device_id: 设备端点 ID。
        mode: ``loopback`` / ``mic`` / ``process`` / None。
        error: 人类可读说明；不得包含音频内容或令牌。
        error_code: 稳定机器码。
        request_id: 关联的请求 ID。

    Returns:
        未序列化的 ``audio.status`` 事件。
    """
    return AudioStatusEvent(
        v=PROTOCOL_VERSION,
        type="audio.status",
        event_id=new_event_id(),
        request_id=request_id,
        ts=now_ms(),
        payload=AudioStatusPayload(
            state=state,
            source=source,
            device_id=device_id,
            mode=mode,
            since=since,
            error=error,
            error_code=error_code,
            sources=sources,
        ),
    )

def make_asr_status(
    *,
    state: str,
    since: int,
    provider: str | None = None,
    error_code: str | None = None,
    error: str | None = None,
) -> AsrStatusEvent:
    """构造 ``asr.status`` 事件；与采集状态相互独立。"""
    return AsrStatusEvent(
        v=PROTOCOL_VERSION,
        type="asr.status",
        event_id=new_event_id(),
        ts=now_ms(),
        payload={
            "state": state,
            "since": since,
            "provider": provider,
            "errorCode": error_code,
            "error": error,
        },
    )


def make_asr_result(
    event_type: str,
    *,
    segment_id: str,
    source: str,
    start_ts_ns: int,
    end_ts_ns: int,
    revision: int,
    text: str,
    confidence: float | None = None,
    provider: str | None = None,
    language: str | None = None,
) -> AsrPartialEvent | AsrFinalEvent:
    """构造 ``asr.partial`` / ``asr.final``；两者共用 segmentId 与采集时间轴。"""
    payload = {
        "segmentId": segment_id,
        "source": source,
        "startTsNs": start_ts_ns,
        "endTsNs": end_ts_ns,
        "revision": revision,
        "text": text,
        "confidence": confidence,
        "provider": provider,
        "language": language,
    }
    model = AsrPartialEvent if event_type == "asr.partial" else AsrFinalEvent
    return model(
        v=PROTOCOL_VERSION,
        type=event_type,
        event_id=new_event_id(),
        ts=now_ms(),
        payload=payload,
    )


def make_asr_error(
    *,
    code: str,
    message: str,
    retryable: bool = False,
    segment_id: str | None = None,
) -> AsrErrorEvent:
    """构造 ``asr.error``；不包含识别正文或密钥。"""
    return AsrErrorEvent(
        v=PROTOCOL_VERSION,
        type="asr.error",
        event_id=new_event_id(),
        ts=now_ms(),
        payload={
            "code": code,
            "message": message,
            "segmentId": segment_id,
            "retryable": retryable,
        },
    )


def make_event(event_type: str, payload: dict[str, Any], request_id: str | None = None) -> EventV1:
    """按协议构造任意事件并做一次结构校验。

    Args:
        event_type: 事件类型，例如 ``capture.start``。
        payload: 负载字典。
        request_id: 关联的请求 ID。

    Returns:
        校验通过的具体事件模型。

    Raises:
        ProtocolViolation: 事件类型未知或负载非法。
    """
    return parse_event(
        {
            "v": PROTOCOL_VERSION,
            "type": event_type,
            "eventId": new_event_id(),
            "requestId": request_id,
            "ts": now_ms(),
            "payload": payload,
        }
    )