import sqlite3
from dataclasses import dataclass


class SlotBusy(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SlotOccupancy:
    slot_id: str
    generation: int
    capture_id: str
    raw: bytes


def get_occupancy(db: sqlite3.Connection, slot_id: str) -> SlotOccupancy | None:
    row = db.execute(
        'SELECT slot_id, generation, capture_id, raw FROM device_slots WHERE slot_id = ?',
        (slot_id,),
    ).fetchone()
    if row is None:
        raise KeyError(slot_id)
    return None if row[2] is None else SlotOccupancy(*row)


def occupy_slot(
    db: sqlite3.Connection, capture_id: str, raw: bytes, slot_id: str | None = None,
) -> SlotOccupancy:
    """Allocate within the device recording transaction."""
    if slot_id is None:
        row = db.execute(
            'SELECT slot_id FROM device_slots WHERE capture_id IS NULL ORDER BY slot_id LIMIT 1'
        ).fetchone()
        if row is None:
            raise SlotBusy('all slots occupied')
        slot_id = row[0]
    elif get_occupancy(db, slot_id) is not None:
        raise SlotBusy(slot_id)

    db.execute(
        'UPDATE device_slots SET generation = generation + 1, capture_id = ?, raw = ? '
        'WHERE slot_id = ?',
        (capture_id, raw, slot_id),
    )
    return get_occupancy(db, slot_id)


def clear_slot(db: sqlite3.Connection, slot_id: str, generation: int, capture_id: str) -> None:
    """Clear only the identified occupancy within the device release transaction."""
    changed = db.execute(
        'UPDATE device_slots SET capture_id = NULL, raw = NULL '
        'WHERE slot_id = ? AND generation = ? AND capture_id = ?',
        (slot_id, generation, capture_id),
    ).rowcount
    if changed != 1:
        raise ValueError('target occupancy is no longer live')
