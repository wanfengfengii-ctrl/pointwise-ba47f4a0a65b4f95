import sqlite3
from dataclasses import dataclass

from measurement.units import decode_integer, encode_integer
from recorder.original import CapturedOriginal, CaptureOrigin


@dataclass(frozen=True, slots=True)
class PublishedPiece:
    batch_id: str
    shift_id: str
    original: CapturedOriginal
    micrometers: int
    first_receipt: str
    sequence: int
    read_location: tuple[str, int] | None = None


_PIECES = (
    'SELECT p.batch_id, p.shift_id, p.capture_id, p.source, p.slot_id, p.generation, '
    'p.digest, p.raw, p.micrometers, r.token, p.sequence, p.read_slot, p.read_generation, '
    'p.movable '
    'FROM published_pieces AS p JOIN first_receipts AS r USING (batch_id)'
)


def _read_snapshot(row: tuple) -> PublishedPiece:
    (batch_id, shift_id, capture_id, source, slot_id, generation, digest, raw, value,
     token, sequence, read_slot, read_generation, movable) = row
    origin = CaptureOrigin(capture_id, batch_id, source, slot_id, generation, digest,
                           movable=bool(movable))
    location = None if read_slot is None else (read_slot, read_generation)
    return PublishedPiece(batch_id, shift_id, CapturedOriginal(origin, raw),
                          decode_integer(value), token, sequence, location)


def read_piece(db: sqlite3.Connection, batch_id: str) -> PublishedPiece | None:
    row = db.execute(_PIECES + ' WHERE p.batch_id = ?', (batch_id,)).fetchone()
    return _read_snapshot(row) if row is not None else None


def read_shift_pieces(db: sqlite3.Connection, shift_id: str) -> list[PublishedPiece]:
    rows = db.execute(_PIECES + ' WHERE p.shift_id = ? ORDER BY p.sequence', (shift_id,))
    return [_read_snapshot(row) for row in rows]


def commit_piece(
    db: sqlite3.Connection, batch_id: str, shift_id: str, original: CapturedOriginal,
    micrometers: int, read_location: tuple[str, int] | None = None,
) -> PublishedPiece:
    """Persist the snapshot qualified by publish, with its first receipt."""
    origin = original.origin
    if origin.batch_id != batch_id or type(micrometers) is not int:
        raise ValueError('invalid publication snapshot')
    if read_location is not None and (
        not isinstance(read_location, tuple) or len(read_location) != 2
        or not isinstance(read_location[0], str) or type(read_location[1]) is not int
    ):
        raise ValueError('invalid read location')
    db.execute('BEGIN IMMEDIATE')
    with db:
        prior = read_piece(db, batch_id)
        if prior is not None:
            if (prior.shift_id, prior.original, prior.micrometers, prior.read_location) != (
                shift_id, original, micrometers, read_location,
            ):
                raise ValueError('publication already committed with different evidence')
            return prior
        token = f'first/{batch_id}'
        db.execute('INSERT INTO first_receipts (batch_id, token) VALUES (?, ?)', (batch_id, token))
        sequence = db.execute(
            'INSERT INTO published_pieces '
            '(batch_id, shift_id, capture_id, source, slot_id, generation, digest, raw, micrometers, '
            'movable, read_slot, read_generation) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (batch_id, shift_id, origin.capture_id, origin.source, origin.slot_id,
             origin.generation, origin.digest, original.raw, encode_integer(micrometers),
             int(origin.movable),
             None if read_location is None else read_location[0],
             None if read_location is None else read_location[1]),
        ).lastrowid
        return PublishedPiece(batch_id, shift_id, original, micrometers, token, sequence,
                              read_location)
