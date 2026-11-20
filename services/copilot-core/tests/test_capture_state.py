"""采集状态机：合法迁移、非法迁移与错误态。"""

from __future__ import annotations

import pytest
from copilot_core.capture.state import (
    ALLOWED_TRANSITIONS,
    CaptureState,
    CaptureStateMachine,
    InvalidTransition,
)


def test_initial_state_is_idle() -> None:
    machine = CaptureStateMachine()
    assert machine.state is CaptureState.IDLE
    assert machine.error_code is None


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (CaptureState.IDLE, CaptureState.STARTING),
        (CaptureState.STARTING, CaptureState.CAPTURING),
        (CaptureState.STARTING, CaptureState.STOPPING),
        (CaptureState.STARTING, CaptureState.ERROR),
        (CaptureState.CAPTURING, CaptureState.STOPPING),
        (CaptureState.CAPTURING, CaptureState.ERROR),
        (CaptureState.STOPPING, CaptureState.IDLE),
        (CaptureState.STOPPING, CaptureState.ERROR),
        (CaptureState.ERROR, CaptureState.IDLE),
        (CaptureState.ERROR, CaptureState.STARTING),
    ],
)
def test_allowed_transitions(source: CaptureState, target: CaptureState) -> None:
    machine = CaptureStateMachine(state=source)
    assert machine.can(target)
    machine.transition(target)
    assert machine.state is target


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (CaptureState.IDLE, CaptureState.CAPTURING),
        (CaptureState.IDLE, CaptureState.STOPPING),
        (CaptureState.IDLE, CaptureState.ERROR),
        (CaptureState.CAPTURING, CaptureState.IDLE),
        (CaptureState.CAPTURING, CaptureState.STARTING),
        (CaptureState.STOPPING, CaptureState.CAPTURING),
        (CaptureState.STOPPING, CaptureState.STARTING),
    ],
)
def test_illegal_transitions_raise(source: CaptureState, target: CaptureState) -> None:
    machine = CaptureStateMachine(state=source)
    assert not machine.can(target)
    with pytest.raises(InvalidTransition):
        machine.transition(target)
    assert machine.state is source


def test_transition_table_is_exhaustive() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(CaptureState)


def test_fail_records_code_and_message() -> None:
    machine = CaptureStateMachine()
    machine.transition(CaptureState.STARTING)
    machine.fail("capture_timeout", "start timed out")
    assert machine.state is CaptureState.ERROR
    assert machine.error_code == "capture_timeout"
    assert machine.error_message == "start timed out"


def test_recovering_from_error_clears_error() -> None:
    machine = CaptureStateMachine()
    machine.fail("sidecar_exited", "gone")
    machine.transition(CaptureState.IDLE)
    assert machine.error_code is None
    assert machine.error_message is None
