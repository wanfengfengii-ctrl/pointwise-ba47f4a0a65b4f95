import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from recorder.capture import read_origin
from recorder.original import CaptureOrigin
from recorder.retained import read_retained_original
from recorder.slots import clear_slot


class ReleaseReplyLost(ConnectionError):
    pass


@dataclass(frozen=True, slots=True)
class ReleasePermit:
    origin: CaptureOrigin
    method: str
    reference: str


@dataclass(frozen=True, slots=True)
class ReleaseOutcome:
    operation_id: str
    capture_id: str
    slot_id: str
    generation: int


def query_release(db: sqlite3.Connection, operation_id: str) -> ReleaseOutcome | None:
    row = db.execute(
        'SELECT operation_id, capture_id, slot_id, generation '
        'FROM device_releases WHERE operation_id = ?',
        (operation_id,),
    ).fetchone()
    return ReleaseOutcome(*row) if row is not None else None


def release_capture(
    db: sqlite3.Connection, operation_id: str, permit: ReleasePermit | None, *,
    verify_station: Callable[[ReleasePermit], bool] | None = None,
    lose_reply: bool = False,
) -> ReleaseOutcome:
    """Release one occupancy. The station port verifies committed station copies."""
    if not isinstance(operation_id, str) or not operation_id or permit is None:
        raise ValueError('release needs an operation number and preservation permit')
    origin = permit.origin
    desired = ReleaseOutcome(operation_id, origin.capture_id, origin.slot_id, origin.generation)
    db.execute('BEGIN IMMEDIATE')
    with db:
        prior = query_release(db, operation_id)
        if prior is not None:
            if prior != desired or read_origin(db, origin.capture_id) != origin:
                raise ValueError('device release operation already bound')
            return prior
        if read_origin(db, origin.capture_id) != origin:
            raise ValueError('release permit refers to a different recording')
        if permit.method == 'device':
            retained = read_retained_original(db, origin.capture_id)
            protected = retained is not None and retained.reference == permit.reference
        elif permit.method == 'station':
            protected = verify_station is not None and verify_station(permit)
        else:
            protected = False
        if not protected:
            raise ValueError('no confirmed preservation for target capture')

        clear_slot(db, origin.slot_id, origin.generation, origin.capture_id)
        db.execute(
            'INSERT INTO device_releases (operation_id, capture_id, slot_id, generation) '
            'VALUES (?, ?, ?, ?)',
            (operation_id, origin.capture_id, origin.slot_id, origin.generation),
        )
    if lose_reply:
        raise ReleaseReplyLost(operation_id)
    return desired
