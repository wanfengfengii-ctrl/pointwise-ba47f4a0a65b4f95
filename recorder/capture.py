import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from recorder.original import CapturedOriginal, CaptureOrigin, fingerprint_original
from recorder.packet import encode_live_packet
from recorder.slots import SlotBusy, clear_slot, find_occupancy, get_occupancy, occupy_slot


@dataclass(frozen=True, slots=True)
class MoveOutcome:
    operation_id: str
    capture_id: str
    from_slot: str
    from_generation: int
    to_slot: str
    to_generation: int


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
        'SELECT capture_id, batch_id, source, slot_id, generation, digest, movable '
        'FROM device_origins WHERE capture_id = ?',
        (capture_id,),
    ).fetchone()
    return CaptureOrigin(*row[:6], movable=bool(row[6])) if row is not None else None


def record(
    db: sqlite3.Connection, batch_id: str, capture_id: str, raw: bytes,
    source: str, *, slot_id: str | None = None, movable: bool = False,
) -> CaptureOrigin:
    if not all(isinstance(value, str) and value for value in (batch_id, capture_id, source)):
        raise ValueError('batch, capture and source must be nonempty strings')
    if not isinstance(raw, bytes):
        raise TypeError('raw original must be bytes')
    if type(movable) is not bool:
        raise ValueError('movable must be a boolean')
    digest = fingerprint_original(raw)
    db.execute('BEGIN IMMEDIATE')
    with db:
        prior = read_origin(db, capture_id)
        if prior is not None:
            if (prior.batch_id, prior.source, prior.digest, prior.movable) != (
                batch_id, source, digest, movable,
            ):
                raise ValueError('recording conflicts with existing capture')
            if slot_id is not None and slot_id != prior.slot_id:
                raise ValueError('recording conflicts with original slot')
            return prior

        occupancy = occupy_slot(db, capture_id, raw, slot_id)
        origin = CaptureOrigin(capture_id, batch_id, source, occupancy.slot_id,
                               occupancy.generation, digest, movable)
        db.execute(
            'INSERT INTO device_origins '
            '(capture_id, batch_id, source, slot_id, generation, digest, movable) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (origin.capture_id, origin.batch_id, origin.source, origin.slot_id,
             origin.generation, origin.digest, int(movable)),
        )
        return origin


def read_live_original(db: sqlite3.Connection, capture_id: str) -> CapturedOriginal | None:
    origin = read_origin(db, capture_id)
    if origin is None:
        return None
    occupancy = find_occupancy(db, capture_id)
    if occupancy is None:
        return None
    if fingerprint_original(occupancy.raw) != origin.digest:
        raise ValueError('live original differs from recording')
    return CapturedOriginal(origin, occupancy.raw)


def query_move(db: sqlite3.Connection, operation_id: str) -> MoveOutcome | None:
    row = db.execute(
        'SELECT operation_id, capture_id, from_slot, from_generation, to_slot, to_generation '
        'FROM device_moves WHERE operation_id = ?',
        (operation_id,),
    ).fetchone()
    return MoveOutcome(*row) if row is not None else None


def move_capture(
    db: sqlite3.Connection, operation_id: str, capture_id: str, target_slot: str, *,
    before_commit: Callable[[], None] | None = None,
) -> MoveOutcome:
    """Move one not-yet-released movable capture to another empty slot."""
    if not all(isinstance(value, str) and value
               for value in (operation_id, capture_id, target_slot)):
        raise ValueError('move needs nonempty operation, capture and target slot')
    if before_commit is not None and not callable(before_commit):
        raise TypeError('before_commit must be callable')

    db.execute('BEGIN IMMEDIATE')
    try:
        prior = query_move(db, operation_id)
        if prior is not None:
            if (prior.capture_id, prior.to_slot) != (capture_id, target_slot):
                raise ValueError('device move operation already bound')
            db.commit()
            return prior

        origin = read_origin(db, capture_id)
        if origin is None:
            raise KeyError(capture_id)
        if not origin.movable:
            raise ValueError('capture is not movable')
        if db.execute('SELECT 1 FROM device_moves WHERE capture_id = ?',
                      (capture_id,)).fetchone() is not None:
            raise ValueError('capture was already moved once')
        if db.execute('SELECT 1 FROM device_releases WHERE capture_id = ?',
                      (capture_id,)).fetchone() is not None:
            raise ValueError('capture was already released')

        live = find_occupancy(db, capture_id)
        if live is None or (live.slot_id, live.generation) != (origin.slot_id, origin.generation):
            raise ValueError('capture is no longer live at its announced slot')
        if target_slot == origin.slot_id or get_occupancy(db, target_slot) is not None:
            raise SlotBusy(target_slot)
        target = db.execute(
            'SELECT generation FROM device_slots WHERE slot_id = ?', (target_slot,),
        ).fetchone()
        if target is None:
            raise KeyError(target_slot)
        new_generation = target[0] + 1

        clear_slot(db, live.slot_id, live.generation, capture_id)
        db.execute(
            'UPDATE device_slots SET generation = ?, capture_id = ?, raw = ? WHERE slot_id = ?',
            (new_generation, capture_id, live.raw, target_slot),
        )
        db.execute(
            'INSERT INTO device_moves '
            '(operation_id, capture_id, from_slot, from_generation, to_slot, to_generation) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (operation_id, capture_id, live.slot_id, live.generation, target_slot, new_generation),
        )
        if before_commit is not None:
            before_commit()
        db.commit()
        return MoveOutcome(operation_id, capture_id, live.slot_id, live.generation,
                           target_slot, new_generation)
    except BaseException:
        db.rollback()
        raise


def read_live_packet(db: sqlite3.Connection, capture_id: str, *, seal: Callable[[bytes], object]):
    """Read the live slot of a movable capture and seal a packet for the station."""
    if not callable(seal):
        raise TypeError('seal must be callable')
    origin = read_origin(db, capture_id)
    if origin is None:
        return None
    if not origin.movable:
        raise ValueError('capture is not movable')
    occupancy = find_occupancy(db, capture_id)
    if occupancy is None:
        return None
    if fingerprint_original(occupancy.raw) != origin.digest:
        raise ValueError('live original differs from recording')
    return seal(encode_live_packet(origin, occupancy.raw, occupancy.slot_id, occupancy.generation))
