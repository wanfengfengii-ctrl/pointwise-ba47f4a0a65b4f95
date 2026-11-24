import sqlite3
from dataclasses import dataclass

from measurement.units import decode_integer, encode_integer


@dataclass(frozen=True, slots=True)
class ShiftPolicy:
    shift_id: str
    zero_offset: int
    micrometers_per_count: int
    bad_quality_mask: int


def get_policy(db: sqlite3.Connection, shift_id: str) -> ShiftPolicy:
    row = db.execute(
        'SELECT zero_offset, micrometers_per_count, bad_quality_mask '
        'FROM shift_policies WHERE shift_id = ?', (shift_id,),
    ).fetchone()
    if row is None:
        raise KeyError(shift_id)
    return ShiftPolicy(shift_id, *(decode_integer(value) for value in row))


def open_shift(
    db: sqlite3.Connection, shift_id: str, zero_offset: int,
    micrometers_per_count: int, bad_quality_mask: int,
) -> ShiftPolicy:
    if not isinstance(shift_id, str) or not shift_id:
        raise ValueError('shift must be a nonempty string')
    values = (zero_offset, micrometers_per_count, bad_quality_mask)
    if any(type(value) is not int for value in values):
        raise ValueError('shift policy requires integers')
    if micrometers_per_count <= 0 or bad_quality_mask < 0:
        raise ValueError('scale must be positive and quality mask nonnegative')
    desired = ShiftPolicy(shift_id, *values)
    db.execute('BEGIN IMMEDIATE')
    with db:
        db.execute(
            'INSERT INTO shift_policies (shift_id, zero_offset, micrometers_per_count, bad_quality_mask) '
            'VALUES (?, ?, ?, ?) ON CONFLICT(shift_id) DO NOTHING',
            (shift_id, *(encode_integer(value) for value in values)),
        )
        existing = get_policy(db, shift_id)
        if existing != desired:
            raise ValueError('shift policy already frozen with different parameters')
        return existing
