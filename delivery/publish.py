import sqlite3

from delivery.commit import PublishedPiece, commit_piece
from delivery.replay import find_replay
from intake.receipts import CaptureReceipt
from review.batch import get_batch
from review.decision import read_decision
from review.readiness import assess_readiness


class RejectedMeasurement(ValueError):
    pass


def publish(
    db: sqlite3.Connection, batch_id: str, *, selected_capture_id: str | None = None,
    receipt: CaptureReceipt | None = None,
) -> PublishedPiece | None:
    batch = get_batch(db, batch_id)
    decision = read_decision(db, batch_id)
    if decision is None:
        if selected_capture_id is not None or receipt is not None:
            raise ValueError('no formal choice to publish')
        return None
    if selected_capture_id is not None and selected_capture_id != decision.capture_id:
        raise ValueError('requested capture is not the formal choice')
    if receipt is not None and (
        receipt.original.origin.capture_id != decision.capture_id
        or receipt.original.origin.batch_id != batch_id
    ):
        raise ValueError('submitted receipt is for a different capture')

    existing = find_replay(db, decision, receipt)
    if existing is not None:
        return existing

    readiness = assess_readiness(db, batch_id, decision.capture_id)
    if receipt is not None and receipt != readiness.receipt:
        raise ValueError('submitted receipt does not match committed actual receipt')
    if readiness.status == 'pending':
        return None
    if readiness.status == 'rejected':
        raise RejectedMeasurement(readiness.reason)
    actual = readiness.receipt
    return commit_piece(db, batch_id, batch.shift_id, actual.original,
                        actual.measurement.micrometers, actual.read_location)
