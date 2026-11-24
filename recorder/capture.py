import sqlite3
from pathlib import Path

from recorder.original import CapturedOriginal, CaptureOrigin, fingerprint_original
from recorder.slots import get_occupancy, occupy_slot


def open_device(path: str | Path) -> sqlite3.Connection:
    schema = Path(__file__).resolve().parents[1] / 'sql' / 'device.sql'
    db = sqlite3.connect(path)
    try:
        db.executescript(schema.read_text(encoding='utf-8'))
    except BaseException:
        db.close()
        raise
    return db


def read_origin(db: sqlite3.Connection, capture_id: str) -> CaptureOrigin | None:
    row = db.execute(
        'SELECT capture_id, batch_id, source, slot_id, generation, digest '
        'FROM device_origins WHERE capture_id = ?',
        (capture_id,),
    ).fetchone()
    return CaptureOrigin(*row) if row is not None else None


def record(
    db: sqlite3.Connection, batch_id: str, capture_id: str, raw: bytes,
    source: str, *, slot_id: str | None = None,
) -> CaptureOrigin:
    if not all(isinstance(value, str) and value for value in (batch_id, capture_id, source)):
        raise ValueError('batch, capture and source must be nonempty strings')
    if not isinstance(raw, bytes):
        raise TypeError('raw original must be bytes')
    digest = fingerprint_original(raw)
    db.execute('BEGIN IMMEDIATE')
    with db:
        prior = read_origin(db, capture_id)
        if prior is not None:
            if (prior.batch_id, prior.source, prior.digest) != (batch_id, source, digest):
                raise ValueError('recording conflicts with existing capture')
            if slot_id is not None and slot_id != prior.slot_id:
                raise ValueError('recording conflicts with original slot')
            return prior

        occupancy = occupy_slot(db, capture_id, raw, slot_id)
        origin = CaptureOrigin(capture_id, batch_id, source, occupancy.slot_id,
                               occupancy.generation, digest)
        db.execute(
            'INSERT INTO device_origins (capture_id, batch_id, source, slot_id, generation, digest) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (origin.capture_id, origin.batch_id, origin.source, origin.slot_id,
             origin.generation, origin.digest),
        )
        return origin


def read_live_original(db: sqlite3.Connection, capture_id: str) -> CapturedOriginal | None:
    origin = read_origin(db, capture_id)
    if origin is None:
        return None
    occupancy = get_occupancy(db, origin.slot_id)
    if occupancy is None or (occupancy.capture_id, occupancy.generation) != (
        capture_id, origin.generation,
    ):
        return None
    if fingerprint_original(occupancy.raw) != origin.digest:
        raise ValueError('live original differs from recording')
    return CapturedOriginal(origin, occupancy.raw)
