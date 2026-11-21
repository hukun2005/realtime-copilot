"""短期音频票据：HMAC 签名 + 过期 + 一次性 nonce。

票据只发给被 Core 拉起的 sidecar（通过环境变量），用于把"知道令牌"与"被授权发送音频"
分开；不影响 ``runtime.json`` 的启动方式，也不写日志。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from enum import StrEnum

#: 票据最大长度；超长输入直接拒绝，避免解析开销与异常放大。
MAX_TICKET_LENGTH = 512


class TicketRejection(StrEnum):
    """票据被拒绝的原因。"""

    MALFORMED = "malformed"
    BAD_SIGNATURE = "bad_signature"
    EXPIRED = "expired"
    REPLAYED = "replayed"
    WRONG_SESSION = "wrong_session"
    WRONG_PURPOSE = "wrong_purpose"
    WRONG_GENERATION = "wrong_generation"


@dataclass(frozen=True, slots=True)
class TicketCheck:
    """校验结果。"""

    ok: bool
    reason: TicketRejection | None = None


class TicketIssuer:
    """签发与校验一次性音频票据。"""

    def __init__(self, *, ttl_seconds: int = 60, now=time.monotonic) -> None:
        self._secret = secrets.token_bytes(32)
        self._ttl = ttl_seconds
        self._now = now
        self._session = ""
        self._used: set[str] = set()
        self._required = False

    @property
    def required(self) -> bool:
        """当前是否要求 audio 连接携带票据。"""
        return self._required

    def begin_session(self) -> None:
        """开始一次采集会话：换新 session id 并清空已用 nonce（旧票据随之失效）。"""
        self._session = secrets.token_hex(8)
        self._used.clear()
        self._required = True

    def end_session(self) -> None:
        """结束会话：撤销所有已签发票据。"""
        self._session = ""
        self._used.clear()
        self._required = False

    def issue(self, purpose: str = "audio", generation: int | None = None) -> str:
        """签发一张票据。

        ``purpose`` 区分用途（audio / asr）；``generation`` 可选绑定采集会话代次，
        用于 ASR 票据在采集重启后自动失效。
        """
        if not self._session:
            raise RuntimeError("no active capture session")
        nonce = secrets.token_hex(12)
        payload: dict[str, object] = {
            "p": purpose,
            "n": nonce,
            "s": self._session,
            "e": int(self._now()) + self._ttl,
        }
        if generation is not None:
            payload["g"] = generation
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        signature = hmac.new(self._secret, body, hashlib.sha256).digest()
        return f"{_b64(body)}.{_b64(signature)}"

    def consume(
        self,
        value: str | None,
        purpose: str = "audio",
        generation: int | None = None,
    ) -> TicketCheck:
        """校验并消费一张票据（成功即失效）。

        ``purpose`` 与签发时不一致会被拒绝；传入 ``generation`` 时还要求票据绑定
        同一代次（未绑定代次的票据不满足带代次校验的请求）。
        """
        if not value or len(value) > MAX_TICKET_LENGTH:
            return TicketCheck(False, TicketRejection.MALFORMED)
        try:
            body_part, signature_part = value.split(".", 1)
            body = _unb64(body_part)
            signature = _unb64(signature_part)
        except (ValueError, TypeError, binascii.Error):
            return TicketCheck(False, TicketRejection.MALFORMED)

        expected = hmac.new(self._secret, body, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, signature):
            return TicketCheck(False, TicketRejection.BAD_SIGNATURE)

        try:
            payload = json.loads(body)
            nonce = str(payload["n"])
            session = str(payload["s"])
            expiry = int(payload["e"])
            ticket_purpose = str(payload["p"])
            ticket_generation = payload.get("g")
        except (ValueError, KeyError, TypeError):
            return TicketCheck(False, TicketRejection.MALFORMED)

        if ticket_purpose != purpose:
            return TicketCheck(False, TicketRejection.WRONG_PURPOSE)
        if purpose == "asr" and generation is None:
            # ASR 票据必须绑定采集代次，避免跨会话复用。
            return TicketCheck(False, TicketRejection.WRONG_GENERATION)
        if generation is not None and (
            ticket_generation is None or int(ticket_generation) != generation
        ):
            return TicketCheck(False, TicketRejection.WRONG_GENERATION)
        if session != self._session:
            return TicketCheck(False, TicketRejection.WRONG_SESSION)
        if nonce in self._used:
            return TicketCheck(False, TicketRejection.REPLAYED)
        if self._now() >= expiry:
            return TicketCheck(False, TicketRejection.EXPIRED)

        self._used.add(nonce)
        return TicketCheck(True)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    # validate=True：非法字符/长度直接抛 binascii.Error，而不是静默解码成空字节。
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)
