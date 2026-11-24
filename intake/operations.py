import sqlite3
from dataclasses import dataclass

from intake.evidence import lookup_origin, read_preservation
from recorder.original import CaptureOrigin
from recorder.release import ReleaseOutcome


@dataclass(frozen=True, slots=True)
class ReleaseIntent:
    operation_id: str
    origin: CaptureOrigin
    outcome: ReleaseOutcome | None


def read_operation(db: sqlite3.Connection, operation_id: str) -> ReleaseIntent | None:
    row = db.execute(
        'SELECT capture_id, slot_id, generation, confirmed FROM release_intents WHERE operation_id = ?',
        (operation_id,),
    ).fetchone()
    if row is None:
        return None
    capture_id, slot_id, generation, confirmed = row
    origin = lookup_origin(db, capture_id)
    if origin is None or (origin.slot_id, origin.generation) != (slot_id, generation):
        raise ValueError('stored operation conflicts with announced occupancy')
    outcome = ReleaseOutcome(operation_id, capture_id, slot_id, generation) if confirmed else None
    return ReleaseIntent(operation_id, origin, outcome)


def bind_operation(db: sqlite3.Connection, operation_id: str, origin: CaptureOrigin) -> ReleaseIntent:
    if not isinstance(operation_id, str) or not operation_id:
        raise ValueError('operation number must be a nonempty string')
    db.execute('BEGIN IMMEDIATE')
    with db:
        prior = read_operation(db, operation_id)
        if prior is not None:
            if prior.origin != origin:
                raise ValueError('station release operation already bound')
            return prior
        protected = read_preservation(db, origin.capture_id)
        if protected is None or protected.origin != origin:
            raise ValueError('cannot bind release without preservation')
        existing = db.execute('SELECT operation_id FROM release_intents WHERE capture_id = ?',
                              (origin.capture_id,)).fetchone()
        if existing is not None:
            raise ValueError(f'capture already bound to {existing[0]}')
        db.execute(
            'INSERT INTO release_intents (operation_id, capture_id, slot_id, generation) '
            'VALUES (?, ?, ?, ?)',
            (operation_id, origin.capture_id, origin.slot_id, origin.generation),
        )
        return ReleaseIntent(operation_id, origin, None)


def record_outcome(db: sqlite3.Connection, outcome: ReleaseOutcome) -> ReleaseOutcome:
    db.execute('BEGIN IMMEDIATE')
    with db:
        intent = read_operation(db, outcome.operation_id)
        if intent is None:
            raise KeyError(outcome.operation_id)
        origin = intent.origin
        if (origin.capture_id, origin.slot_id, origin.generation) != (
            outcome.capture_id, outcome.slot_id, outcome.generation,
        ):
            raise ValueError('device outcome conflicts with station intent')
        if intent.outcome is None:
            db.execute('UPDATE release_intents SET confirmed = 1 WHERE operation_id = ?',
                       (outcome.operation_id,))
        return outcome
