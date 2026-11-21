"""短期音频票据：签发、一次性消费、过期、撤销与篡改。"""

from __future__ import annotations

import base64
import hashlib
import hmac

import pytest
from copilot_core.tickets import MAX_TICKET_LENGTH, TicketIssuer, TicketRejection


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


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def test_exact_expiry_instant_is_rejected() -> None:
    clock = FakeClock()
    issuer = TicketIssuer(ttl_seconds=60, now=clock)  # type: ignore[arg-type]
    issuer.begin_session()
    ticket = issuer.issue()

    clock.advance(60)

    check = issuer.consume(ticket)
    assert not check.ok
    assert check.reason is TicketRejection.EXPIRED


def test_ticket_valid_one_second_before_expiry() -> None:
    clock = FakeClock()
    issuer = TicketIssuer(ttl_seconds=60, now=clock)  # type: ignore[arg-type]
    issuer.begin_session()
    ticket = issuer.issue()

    clock.advance(59)

    assert issuer.consume(ticket).ok


def test_oversized_input_is_rejected_without_parsing() -> None:
    issuer, _ = make_issuer()
    oversized = "a" * (MAX_TICKET_LENGTH + 1)

    check = issuer.consume(oversized)
    assert not check.ok
    assert check.reason is TicketRejection.MALFORMED


@pytest.mark.parametrize("value", ["!!!.???", "not-base64.signature", "a.b.c"])
def test_malformed_base64_is_rejected(value: str) -> None:
    issuer, _ = make_issuer()
    check = issuer.consume(value)
    assert not check.ok
    assert check.reason is TicketRejection.MALFORMED


def test_malformed_json_with_valid_signature_is_rejected() -> None:
    issuer, _ = make_issuer()
    body = b"this is not json"
    signature = hmac.new(issuer._secret, body, hashlib.sha256).digest()
    value = f"{_b64(body)}.{_b64(signature)}"

    check = issuer.consume(value)
    assert not check.ok
    assert check.reason is TicketRejection.MALFORMED


def test_ticket_from_ended_session_is_rejected() -> None:
    issuer, _ = make_issuer()
    ticket = issuer.issue()
    issuer.end_session()

    check = issuer.consume(ticket)
    assert not check.ok
    assert check.reason is TicketRejection.WRONG_SESSION


def test_new_session_cannot_consume_old_ticket() -> None:
    issuer, _ = make_issuer()
    old = issuer.issue()
    issuer.begin_session()
    current = issuer.issue()

    assert issuer.consume(old).reason is TicketRejection.WRONG_SESSION
    assert issuer.consume(current).ok


def test_rejection_does_not_expose_signature_or_secret() -> None:
    issuer, _ = make_issuer()
    ticket = issuer.issue()
    body, signature = ticket.split(".", 1)

    check = issuer.consume(f"{body}.{signature[:-2]}AA")
    assert not check.ok
    assert check.reason is TicketRejection.BAD_SIGNATURE
    assert signature not in repr(check)
    assert issuer._secret.hex() not in repr(check)
