import sqlite3
from dataclasses import dataclass

from delivery.commit import read_shift_pieces
from measurement.policy import get_policy
from recorder.original import CaptureOrigin


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    batch_id: str
    micrometers: int
    origin: CaptureOrigin
    sequence: int


def shift_ledger(db: sqlite3.Connection, shift_id: str) -> list[LedgerEntry]:
    get_policy(db, shift_id)
    return [LedgerEntry(piece.batch_id, piece.micrometers, piece.original.origin, piece.sequence)
            for piece in read_shift_pieces(db, shift_id)]
