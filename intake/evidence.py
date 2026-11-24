import sqlite3
from dataclasses import dataclass
from pathlib import Path

from recorder.original import CapturedOriginal, CaptureOrigin, fingerprint_original


@dataclass(frozen=True, slots=True)
class PreservationRecord:
    origin: CaptureOrigin
    method: str
    reference: str


def open_station(path: str | Path) -> sqlite3.Connection:
    schema = Path(__file__).resolve().parents[1] / 'sql' / 'station.sql'
    db = sqlite3.connect(path)
    try:
        db.executescript(schema.read_text(encoding='utf-8'))
    except BaseException:
        db.close()
        raise
    return db


def lookup_origin(db: sqlite3.Connection, capture_id: str) -> CaptureOrigin | None:
    row = db.execute(
        'SELECT capture_id, batch_id, source, slot_id, generation, digest, movable '
        'FROM announced_origins WHERE capture_id = ?',
        (capture_id,),
    ).fetchone()
    return CaptureOrigin(*row[:6], movable=bool(row[6])) if row is not None else None


def announce_origin(db: sqlite3.Connection, origin: CaptureOrigin) -> CaptureOrigin:
    """Accept an announcement from the trusted device port."""
    db.execute('BEGIN IMMEDIATE')
    with db:
        prior = lookup_origin(db, origin.capture_id)
        if prior is not None:
            if prior != origin:
                raise ValueError('conflicting capture announcement')
            return prior
        if db.execute('SELECT 1 FROM review_batches WHERE batch_id = ?',
                      (origin.batch_id,)).fetchone() is None:
            raise KeyError(origin.batch_id)
        db.execute(
            'INSERT INTO announced_origins '
            '(capture_id, batch_id, source, slot_id, generation, digest, movable) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (origin.capture_id, origin.batch_id, origin.source, origin.slot_id,
             origin.generation, origin.digest, int(origin.movable)),
        )
        return origin


def verify_original(db: sqlite3.Connection, original: CapturedOriginal) -> CaptureOrigin:
    origin = lookup_origin(db, original.origin.capture_id)
    if origin is None:
        raise KeyError(original.origin.capture_id)
    if origin != original.origin or origin.digest != fingerprint_original(original.raw):
        raise ValueError('original does not match announcement')
    return origin


def read_preservation(db: sqlite3.Connection, capture_id: str) -> PreservationRecord | None:
    row = db.execute(
        'SELECT method, reference FROM preservations WHERE capture_id = ?', (capture_id,),
    ).fetchone()
    if row is None:
        return None
    return PreservationRecord(lookup_origin(db, capture_id), *row)


def bind_preservation(
    db: sqlite3.Connection, original: CapturedOriginal, method: str, reference: str,
) -> PreservationRecord:
    """Register a committed copy: station receipt, device fixed area, or live packet."""
    if method not in ('station', 'device', 'live') or reference != original.origin.capture_id:
        raise ValueError('invalid preservation reference')
    db.execute('BEGIN IMMEDIATE')
    with db:
        origin = verify_original(db, original)
        record = PreservationRecord(origin, method, reference)
        prior = read_preservation(db, origin.capture_id)
        if prior is not None:
            if prior != record:
                raise ValueError('capture already bound to another preservation path')
            return prior
        if method == 'station':
            if origin.movable:
                raise ValueError('movable captures need a live read receipt, not a bare copy')
            row = db.execute('SELECT raw FROM capture_receipts WHERE capture_id = ?',
                             (origin.capture_id,)).fetchone()
            if row is None or row[0] != original.raw:
                raise ValueError('preserved station copy must already be committed')
        elif method == 'device':
            if origin.movable:
                raise ValueError('movable captures cannot be preserved through the device fixed area')
        elif method == 'live':
            row = db.execute(
                'SELECT raw, read_slot FROM capture_receipts WHERE capture_id = ?',
                (origin.capture_id,),
            ).fetchone()
            if row is None or row[0] != original.raw or row[1] is None:
                raise ValueError('live preservation needs a committed read receipt')
        db.execute(
            'INSERT INTO preservations (capture_id, method, reference) VALUES (?, ?, ?)',
            (origin.capture_id, method, reference),
        )
        return record
