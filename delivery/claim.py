import sqlite3

from delivery.commit import PublishedPiece, read_piece
from review.batch import get_batch


def claim(db: sqlite3.Connection, batch_id: str) -> PublishedPiece | None:
    get_batch(db, batch_id)
    return read_piece(db, batch_id)
