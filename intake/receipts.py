import sqlite3
from dataclasses import dataclass

from intake.evidence import lookup_origin, verify_original
from measurement.assessment import MeasurementResult, assess_original
from measurement.policy import get_policy
from measurement.units import decode_integer, encode_integer
from recorder.original import CapturedOriginal


@dataclass(frozen=True, slots=True)
class CaptureReceipt:
    original: CapturedOriginal
    measurement: MeasurementResult


def find_receipt(db: sqlite3.Connection, capture_id: str) -> CaptureReceipt | None:
    row = db.execute(
        'SELECT raw, micrometers, reason FROM capture_receipts WHERE capture_id = ?',
        (capture_id,),
    ).fetchone()
    if row is None:
        return None
    raw, reading, reason = row
    original = CapturedOriginal(lookup_origin(db, capture_id), raw)
    verified_reading = decode_integer(reading) if reading is not None else None
    return CaptureReceipt(original, MeasurementResult(verified_reading, reason))


def receive_original(db: sqlite3.Connection, original: CapturedOriginal) -> CaptureReceipt:
    db.execute('BEGIN IMMEDIATE')
    with db:
        origin = verify_original(db, original)
        prior = find_receipt(db, origin.capture_id)
        if prior is not None:
            if prior.original != original:
                raise ValueError('conflicting actual original')
            return prior

        shift_id = db.execute('SELECT shift_id FROM review_batches WHERE batch_id = ?',
                              (origin.batch_id,)).fetchone()[0]
        measurement = assess_original(original.raw, get_policy(db, shift_id))
        reading = measurement.micrometers
        db.execute(
            'INSERT INTO capture_receipts (capture_id, raw, micrometers, reason) VALUES (?, ?, ?, ?)',
            (origin.capture_id, original.raw,
             encode_integer(reading) if reading is not None else None, measurement.reason),
        )
        return CaptureReceipt(original, measurement)
