import sqlite3
from dataclasses import dataclass

from intake.evidence import lookup_origin
from measurement.policy import get_policy
from recorder.original import CaptureOrigin


@dataclass(frozen=True, slots=True)
class ReviewBatch:
    batch_id: str
    shift_id: str


@dataclass(frozen=True, slots=True)
class BatchCandidate:
    batch_id: str
    origin: CaptureOrigin


def get_batch(db: sqlite3.Connection, batch_id: str) -> ReviewBatch:
    row = db.execute('SELECT shift_id FROM review_batches WHERE batch_id = ?', (batch_id,)).fetchone()
    if row is None:
        raise KeyError(batch_id)
    return ReviewBatch(batch_id, row[0])


def open_batch(db: sqlite3.Connection, batch_id: str, shift_id: str) -> ReviewBatch:
    if not isinstance(batch_id, str) or not batch_id:
        raise ValueError('batch must be a nonempty string')
    db.execute('BEGIN IMMEDIATE')
    with db:
        get_policy(db, shift_id)
        db.execute('INSERT INTO review_batches (batch_id, shift_id) VALUES (?, ?) '
                   'ON CONFLICT(batch_id) DO NOTHING', (batch_id, shift_id))
        batch = get_batch(db, batch_id)
        if batch.shift_id != shift_id:
            raise ValueError('batch already belongs to another shift')
        return batch


def get_candidate(db: sqlite3.Connection, batch_id: str, capture_id: str) -> BatchCandidate:
    get_batch(db, batch_id)
    row = db.execute('SELECT 1 FROM batch_candidates WHERE batch_id = ? AND capture_id = ?',
                     (batch_id, capture_id)).fetchone()
    if row is None:
        raise ValueError('capture is not a candidate for this batch')
    return BatchCandidate(batch_id, lookup_origin(db, capture_id))


def register_candidate(db: sqlite3.Connection, batch_id: str, capture_id: str) -> BatchCandidate:
    db.execute('BEGIN IMMEDIATE')
    with db:
        get_batch(db, batch_id)
        origin = lookup_origin(db, capture_id)
        if origin is None or origin.batch_id != batch_id:
            raise ValueError('capture announcement does not belong to this batch')
        db.execute(
            'INSERT INTO batch_candidates (batch_id, capture_id) VALUES (?, ?) '
            'ON CONFLICT(batch_id, capture_id) DO NOTHING', (batch_id, capture_id),
        )
        return BatchCandidate(batch_id, origin)
