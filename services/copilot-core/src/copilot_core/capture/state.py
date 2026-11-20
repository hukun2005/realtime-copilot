"""采集控制状态机。

状态与迁移是控制面的唯一事实源：Core 决定何时允许 ``capture.start`` / ``capture.stop``，
sidecar 的状态上报只用于确认，不允许跳步。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum


class CaptureState(StrEnum):
    """采集状态。"""

    IDLE = "idle"
    STARTING = "starting"
    CAPTURING = "capturing"
    STOPPING = "stopping"
    ERROR = "error"


#: 合法迁移表；未列出的迁移一律拒绝。
ALLOWED_TRANSITIONS: dict[CaptureState, frozenset[CaptureState]] = {
    CaptureState.IDLE: frozenset({CaptureState.STARTING}),
    CaptureState.STARTING: frozenset({CaptureState.CAPTURING, CaptureState.STOPPING, CaptureState.ERROR}),
    CaptureState.CAPTURING: frozenset({CaptureState.STOPPING, CaptureState.ERROR}),
    CaptureState.STOPPING: frozenset({CaptureState.IDLE, CaptureState.ERROR}),
    CaptureState.ERROR: frozenset({CaptureState.IDLE, CaptureState.STARTING}),
}


class InvalidTransition(Exception):
    """不允许的状态迁移。"""

    def __init__(self, source: CaptureState, target: CaptureState) -> None:
        super().__init__(f"{source.value} -> {target.value} is not allowed")
        self.source = source
        self.target = target


@dataclass
class CaptureStateMachine:
    """带时间戳与错误码的状态机。"""

    state: CaptureState = CaptureState.IDLE
    since_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    error_code: str | None = None
    error_message: str | None = None

    def can(self, target: CaptureState) -> bool:
        """判断迁移是否合法。"""
        return target in ALLOWED_TRANSITIONS[self.state]

    def transition(self, target: CaptureState, *, now_ms: int | None = None) -> None:
        """执行迁移。

        Raises:
            InvalidTransition: 迁移不在允许表中。
        """
        if not self.can(target):
            raise InvalidTransition(self.state, target)
        self.state = target
        self.since_ms = int(time.time() * 1000) if now_ms is None else now_ms
        if target is not CaptureState.ERROR:
            self.error_code = None
            self.error_message = None

    def fail(self, code: str, message: str, *, now_ms: int | None = None) -> None:
        """进入错误态并记录原因；从任意状态都允许（属于安全停机）。"""
        self.state = CaptureState.ERROR
        self.since_ms = int(time.time() * 1000) if now_ms is None else now_ms
        self.error_code = code
        self.error_message = message[:500]
