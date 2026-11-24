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


_PIECES = (
    'SELECT p.batch_id, p.shift_id, p.capture_id, p.source, p.slot_id, p.generation, '
    'p.digest, p.raw, p.micrometers, r.token, p.sequence '
    'FROM published_pieces AS p JOIN first_receipts AS r USING (batch_id)'
)


def _read_snapshot(row: tuple) -> PublishedPiece:
    batch_id, shift_id, capture_id, source, slot_id, generation, digest, raw, value, token, sequence = row
    origin = CaptureOrigin(capture_id, batch_id, source, slot_id, generation, digest)
    return PublishedPiece(batch_id, shift_id, CapturedOriginal(origin, raw),
                          decode_integer(value), token, sequence)


def read_piece(db: sqlite3.Connection, batch_id: str) -> PublishedPiece | None:
    row = db.execute(_PIECES + ' WHERE p.batch_id = ?', (batch_id,)).fetchone()
    return _read_snapshot(row) if row is not None else None


def read_shift_pieces(db: sqlite3.Connection, shift_id: str) -> list[PublishedPiece]:
    rows = db.execute(_PIECES + ' WHERE p.shift_id = ? ORDER BY p.sequence', (shift_id,))
    return [_read_snapshot(row) for row in rows]


def commit_piece(
    db: sqlite3.Connection, batch_id: str, shift_id: str, original: CapturedOriginal,
    micrometers: int,
) -> PublishedPiece:
    """Persist the snapshot qualified by publish, with its first receipt."""
    origin = original.origin
    if origin.batch_id != batch_id or type(micrometers) is not int:
        raise ValueError('invalid publication snapshot')
    db.execute('BEGIN IMMEDIATE')
    with db:
        prior = read_piece(db, batch_id)
        if prior is not None:
            if (prior.shift_id, prior.original, prior.micrometers) != (shift_id, original, micrometers):
                raise ValueError('publication already committed with different evidence')
            return prior
        token = f'first/{batch_id}'
        db.execute('INSERT INTO first_receipts (batch_id, token) VALUES (?, ?)', (batch_id, token))
        sequence = db.execute(
            'INSERT INTO published_pieces '
            '(batch_id, shift_id, capture_id, source, slot_id, generation, digest, raw, micrometers) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (batch_id, shift_id, origin.capture_id, origin.source, origin.slot_id,
             origin.generation, origin.digest, original.raw, encode_integer(micrometers)),
        ).lastrowid
        return PublishedPiece(batch_id, shift_id, original, micrometers, token, sequence)
