from dataclasses import dataclass
from hashlib import sha256


@dataclass(frozen=True, slots=True)
class CaptureOrigin:
    capture_id: str
    batch_id: str
    source: str
    slot_id: str
    generation: int
    digest: str


@dataclass(frozen=True, slots=True)
class CapturedOriginal:
    origin: CaptureOrigin
    raw: bytes


def fingerprint_original(raw: bytes) -> str:
    return sha256(raw).hexdigest()
