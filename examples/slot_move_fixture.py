from collections.abc import Callable
from dataclasses import dataclass, replace
from hmac import compare_digest, digest


SAMPLE = {
    "shift": "S-MOVE",
    "batch": "B-MOVE",
    "zero_offset": 2,
    "scale": 1,
    "bad_quality_mask": 1,
    "raw": b"{\"count\":13,\"quality\":0}",
    "sources": {"X": "head-A/strip-41", "Z": "head-A/strip-43"},
    "expected_micrometers": 11,
}

_DOMAIN = b"strip-station/fixture-frame/v1\x00"


@dataclass(frozen=True, slots=True)
class Envelope:
    body: bytes
    tag: bytes


class LocalPort:
    """Authenticate opaque bytes for the local transport fixture."""

    def __init__(self, key: bytes):
        self._key = key

    def seal(self, body: bytes) -> Envelope:
        if not isinstance(body, bytes):
            raise TypeError("frame body must be bytes")
        return Envelope(body, digest(self._key, _DOMAIN + body, "sha256"))

    def verify(self, envelope: Envelope) -> bytes:
        if (not isinstance(envelope, Envelope) or not isinstance(envelope.body, bytes)
                or not isinstance(envelope.tag, bytes)):
            raise ValueError("invalid frame")
        expected = digest(self._key, _DOMAIN + envelope.body, "sha256")
        if not compare_digest(envelope.tag, expected):
            raise ValueError("frame authentication failed")
        return envelope.body


class HeldFrames:
    def __init__(self):
        self._frames: dict[str, Envelope] = {}

    def hold(self, name: str, frame: Envelope) -> None:
        if name in self._frames:
            raise ValueError("frame name is already held")
        self._frames[name] = frame

    def take(self, name: str) -> Envelope:
        return self._frames.pop(name)


class CommitRefused(RuntimeError):
    pass


class ReplyDropped(ConnectionError):
    pass


def fail_before_commit() -> None:
    raise CommitRefused("injected before commit")


def drop_reply(action: Callable[[], object]) -> None:
    action()
    raise ReplyDropped("injected after action returned")


def main() -> None:
    device = LocalPort(b"test-only-local-key")
    station = LocalPort(b"test-only-local-key")
    mailbox = HeldFrames()
    first = device.seal(b"\x00\xff")
    second = device.seal(b"second")
    mailbox.hold("first", first)
    mailbox.hold("second", second)
    assert station.verify(mailbox.take("second")) == b"second"
    assert station.verify(mailbox.take("first")) == b"\x00\xff"
    for verifier, frame in (
        (station, replace(first, body=b"edited")),
        (LocalPort(b"other-key"), first),
    ):
        try:
            verifier.verify(frame)
        except ValueError:
            pass
        else:
            raise AssertionError("altered frame accepted")
    try:
        fail_before_commit()
    except CommitRefused:
        pass
    else:
        raise AssertionError("failure was not injected")
    ran = []
    try:
        drop_reply(lambda: ran.append("applied"))
    except ReplyDropped:
        assert ran == ["applied"]
    else:
        raise AssertionError("reply was not dropped")
    print("fixture: reverse delivery, altered frame rejection, precommit failure, lost reply")


if __name__ == "__main__":
    main()
