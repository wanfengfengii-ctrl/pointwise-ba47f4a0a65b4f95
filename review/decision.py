import sqlite3
from dataclasses import dataclass

from review.batch import get_batch, get_candidate
from review.readiness import Readiness, assess_readiness


@dataclass(frozen=True, slots=True)
class FormalDecision:
    batch_id: str
    capture_id: str


def read_decision(db: sqlite3.Connection, batch_id: str) -> FormalDecision | None:
    get_batch(db, batch_id)
    row = db.execute('SELECT capture_id FROM formal_decisions WHERE batch_id = ?',
                     (batch_id,)).fetchone()
    return FormalDecision(batch_id, row[0]) if row is not None else None


def decide_batch(db: sqlite3.Connection, batch_id: str, capture_id: str) -> Readiness:
    db.execute('BEGIN IMMEDIATE')
    with db:
        get_candidate(db, batch_id, capture_id)
        prior = read_decision(db, batch_id)
        if prior is None:
            db.execute('INSERT INTO formal_decisions (batch_id, capture_id) VALUES (?, ?)',
                       (batch_id, capture_id))
        elif prior.capture_id != capture_id:
            raise ValueError('formal choice already fixed for this batch')
    return assess_readiness(db, batch_id, capture_id)
