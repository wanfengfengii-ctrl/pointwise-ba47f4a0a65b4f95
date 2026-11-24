import sqlite3
from dataclasses import dataclass

from intake.receipts import find_receipt
from measurement.assessment import MeasurementResult
from review.batch import get_candidate
from review.decision import read_decision


@dataclass(frozen=True, slots=True)
class PreviewSelection:
    batch_id: str
    capture_id: str
    measurement: MeasurementResult | None


def preview(db: sqlite3.Connection, batch_id: str, capture_id: str) -> PreviewSelection:
    db.execute('BEGIN IMMEDIATE')
    with db:
        get_candidate(db, batch_id, capture_id)
        if read_decision(db, batch_id) is not None:
            raise ValueError('batch already finalized')
        db.execute(
            'INSERT INTO preview_choices (batch_id, capture_id) VALUES (?, ?) '
            'ON CONFLICT(batch_id) DO UPDATE SET capture_id = excluded.capture_id',
            (batch_id, capture_id),
        )
        receipt = find_receipt(db, capture_id)
        measurement = receipt.measurement if receipt is not None else None
        return PreviewSelection(batch_id, capture_id, measurement)
