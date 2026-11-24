import sqlite3
from dataclasses import dataclass
from typing import Literal

from intake.receipts import CaptureReceipt, find_receipt
from review.batch import get_candidate


@dataclass(frozen=True, slots=True)
class Readiness:
    status: Literal['pending', 'ready', 'rejected']
    receipt: CaptureReceipt | None = None
    reason: str | None = None


def assess_readiness(db: sqlite3.Connection, batch_id: str, capture_id: str) -> Readiness:
    candidate = get_candidate(db, batch_id, capture_id)
    receipt = find_receipt(db, capture_id)
    if receipt is None:
        return Readiness('pending')
    if receipt.original.origin != candidate.origin:
        raise ValueError('actual receipt conflicts with selected capture')
    if receipt.measurement.reason is not None:
        return Readiness('rejected', receipt, receipt.measurement.reason)
    return Readiness('ready', receipt)
