import sqlite3
from dataclasses import dataclass

from recorder.capture import read_live_original, read_origin
from recorder.original import CapturedOriginal, fingerprint_original


@dataclass(frozen=True, slots=True)
class RetainedOriginal:
    reference: str
    original: CapturedOriginal


def read_retained_original(db: sqlite3.Connection, capture_id: str) -> RetainedOriginal | None:
    row = db.execute(
        'SELECT raw FROM retained_originals WHERE capture_id = ?', (capture_id,),
    ).fetchone()
    if row is None:
        return None
    origin = read_origin(db, capture_id)
    if origin is None or fingerprint_original(row[0]) != origin.digest:
        raise ValueError('fixed original differs from recording')
    return RetainedOriginal(capture_id, CapturedOriginal(origin, row[0]))


def retain_original(db: sqlite3.Connection, capture_id: str) -> RetainedOriginal:
    db.execute('BEGIN IMMEDIATE')
    with db:
        prior = read_retained_original(db, capture_id)
        if prior is not None:
            return prior
        original = read_live_original(db, capture_id)
        if original is None:
            raise LookupError(f'live original unavailable for {capture_id}')
        db.execute(
            'INSERT INTO retained_originals (capture_id, raw) VALUES (?, ?)',
            (capture_id, original.raw),
        )
        return RetainedOriginal(capture_id, original)
