import sqlite3
from dataclasses import dataclass

from intake.evidence import lookup_origin, verify_original
from measurement.assessment import MeasurementResult, assess_original
from measurement.policy import get_policy
from measurement.units import decode_integer, encode_integer
from recorder.original import CapturedOriginal, CaptureOrigin, fingerprint_original
from recorder.packet import decode_live_packet


@dataclass(frozen=True, slots=True)
class CaptureReceipt:
    original: CapturedOriginal
    measurement: MeasurementResult
    read_location: tuple[str, int] | None = None


def find_receipt(db: sqlite3.Connection, capture_id: str) -> CaptureReceipt | None:
    row = db.execute(
        'SELECT raw, micrometers, reason, read_slot, read_generation '
        'FROM capture_receipts WHERE capture_id = ?',
        (capture_id,),
    ).fetchone()
    if row is None:
        return None
    raw, reading, reason, read_slot, read_generation = row
    original = CapturedOriginal(lookup_origin(db, capture_id), raw)
    verified_reading = decode_integer(reading) if reading is not None else None
    location = None if read_slot is None else (read_slot, read_generation)
    return CaptureReceipt(original, MeasurementResult(verified_reading, reason), location)


def _assess(db: sqlite3.Connection, origin: CaptureOrigin, raw: bytes) -> MeasurementResult:
    shift_id = db.execute('SELECT shift_id FROM review_batches WHERE batch_id = ?',
                          (origin.batch_id,)).fetchone()[0]
    return assess_original(raw, get_policy(db, shift_id))


def _commit_receipt(
    db: sqlite3.Connection, origin: CaptureOrigin, raw: bytes,
    location: tuple[str, int] | None,
) -> CaptureReceipt:
    measurement = _assess(db, origin, raw)
    reading = measurement.micrometers
    db.execute(
        'INSERT INTO capture_receipts '
        '(capture_id, raw, micrometers, reason, read_slot, read_generation) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (origin.capture_id, raw,
         encode_integer(reading) if reading is not None else None, measurement.reason,
         None if location is None else location[0], None if location is None else location[1]),
    )
    return CaptureReceipt(CapturedOriginal(origin, raw), measurement, location)


def receive_original(db: sqlite3.Connection, original: CapturedOriginal) -> CaptureReceipt:
    """Receive a bare actual original (the non-movable legacy path)."""
    db.execute('BEGIN IMMEDIATE')
    with db:
        origin = verify_original(db, original)
        if origin.movable:
            raise ValueError('movable captures must be received through their live packet')
        prior = find_receipt(db, origin.capture_id)
        if prior is not None:
            if prior.original != original:
                raise ValueError('conflicting actual original')
            return prior
        return _commit_receipt(db, origin, original.raw, None)


def receive_live_packet(
    db: sqlite3.Connection, capture_id: str, packet: object, *, verify,
) -> CaptureReceipt:
    """Receive an authenticated live read packet for a movable capture."""
    if not callable(verify):
        raise TypeError('verify must be callable')
    body = verify(packet)
    if not isinstance(body, (bytes, bytearray)):
        raise ValueError('verified packet body is not bytes')
    fields = decode_live_packet(body)

    db.execute('BEGIN IMMEDIATE')
    with db:
        prior = find_receipt(db, capture_id)
        origin = lookup_origin(db, capture_id)
        if origin is None:
            raise KeyError(capture_id)
        if not origin.movable:
            raise ValueError('capture is not movable')
        if (fields.capture_id, fields.batch_id, fields.source, fields.digest) != (
            origin.capture_id, origin.batch_id, origin.source, origin.digest,
        ):
            raise ValueError('packet does not belong to the announced capture')
        if fingerprint_original(fields.raw) != origin.digest:
            raise ValueError('packet bytes do not match the announced original')

        location = (fields.slot_id, fields.generation)
        if prior is not None:
            if prior.original.raw != fields.raw:
                raise ValueError('conflicting actual original')
            if prior.read_location is None:
                raise ValueError('committed receipt has no matching read address')
            # A later genuine packet from another slot replays the first receipt.
            return prior
        return _commit_receipt(db, origin, fields.raw, location)
