"""短期音频票据：签发、一次性消费、过期、撤销与篡改。"""

from __future__ import annotations

import pytest
from copilot_core.tickets import TicketIssuer, TicketRejection


class FakeClock:
    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def make_issuer(**overrides: object) -> tuple[TicketIssuer, FakeClock]:
    clock = FakeClock()
    issuer = TicketIssuer(ttl_seconds=60, now=clock)  # type: ignore[arg-type]
    issuer.begin_session()
    return issuer, clock


def test_ticket_roundtrip_is_accepted_once() -> None:
    issuer, _ = make_issuer()
    ticket = issuer.issue()

    assert issuer.consume(ticket).ok
    replay = issuer.consume(ticket)
    assert not replay.ok
    assert replay.reason is TicketRejection.REPLAYED


def test_ticket_requires_active_session() -> None:
    issuer = TicketIssuer()
    with pytest.raises(RuntimeError):
        issuer.issue()


def test_expired_ticket_is_rejected() -> None:
    issuer, clock = make_issuer()
    ticket = issuer.issue()
    clock.advance(120)

    check = issuer.consume(ticket)
    assert not check.ok
    assert check.reason is TicketRejection.EXPIRED


def test_tampered_ticket_is_rejected() -> None:
    issuer, _ = make_issuer()
    ticket = issuer.issue()
    body, signature = ticket.split(".", 1)
    tampered = f"{body[:-2]}AA.{signature}"

    check = issuer.consume(tampered)
    assert not check.ok
    assert check.reason in {TicketRejection.BAD_SIGNATURE, TicketRejection.MALFORMED}


def test_ticket_from_previous_session_is_rejected() -> None:
    issuer, _ = make_issuer()
    old = issuer.issue()
    issuer.begin_session()

    check = issuer.consume(old)
    assert not check.ok
    assert check.reason is TicketRejection.WRONG_SESSION


def test_end_session_revokes_and_disables_requirement() -> None:
    issuer, _ = make_issuer()
    ticket = issuer.issue()
    issuer.end_session()

    assert issuer.required is False
    assert not issuer.consume(ticket).ok


def test_malformed_values_are_rejected() -> None:
    issuer, _ = make_issuer()
    for value in (None, "", "no-dot", "a.b.c"):
        check = issuer.consume(value)
        assert not check.ok
        assert check.reason is TicketRejection.MALFORMED


def test_required_flag_tracks_session() -> None:
    issuer = TicketIssuer()
    assert issuer.required is False
    issuer.begin_session()
    assert issuer.required is True
    issuer.end_session()
    assert issuer.required is False
