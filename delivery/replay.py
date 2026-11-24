import sqlite3

from delivery.commit import PublishedPiece, read_piece
from intake.receipts import CaptureReceipt
from review.decision import FormalDecision


def find_replay(
    db: sqlite3.Connection, decision: FormalDecision, receipt: CaptureReceipt | None = None,
) -> PublishedPiece | None:
    piece = read_piece(db, decision.batch_id)
    if piece is None:
        return None
    if piece.original.origin.capture_id != decision.capture_id:
        raise ValueError('replay does not match formal choice')
    if receipt is not None and (
        receipt.original != piece.original or receipt.measurement.reason is not None
        or receipt.measurement.micrometers != piece.micrometers
    ):
        raise ValueError('replay does not match committed receipt')
    return piece
