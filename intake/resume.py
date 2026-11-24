import sqlite3

from intake.evidence import read_preservation
from intake.operations import read_operation, record_outcome
from intake.receipts import find_receipt
from recorder.original import fingerprint_original
from recorder.release import ReleaseOutcome, ReleasePermit, query_release, release_capture
from recorder.slots import get_occupancy


def resume_release(
    station: sqlite3.Connection, device: sqlite3.Connection, operation_id: str, *,
    retry: bool = False, lose_reply: bool = False,
) -> ReleaseOutcome | None:
    intent = read_operation(station, operation_id)
    if intent is None:
        raise KeyError(operation_id)
    if intent.outcome is not None:
        return intent.outcome
    outcome = query_release(device, operation_id)
    if outcome is not None:
        return record_outcome(station, outcome)
    if not retry:
        return None

    origin = intent.origin
    saved = read_preservation(station, origin.capture_id)
    if saved is None or saved.origin != origin:
        return None
    active = get_occupancy(device, origin.slot_id)
    if active is None or (active.capture_id, active.generation) != (origin.capture_id, origin.generation):
        # Another caller may have committed the release since the first query.
        outcome = query_release(device, operation_id)
        return record_outcome(station, outcome) if outcome is not None else None

    def verify_station(permit: ReleasePermit) -> bool:
        receipt = find_receipt(station, permit.origin.capture_id)
        return (receipt is not None and receipt.original.origin == permit.origin
                and permit.reference == permit.origin.capture_id
                and fingerprint_original(receipt.original.raw) == permit.origin.digest)

    outcome = release_capture(
        device, operation_id, ReleasePermit(origin, saved.method, saved.reference),
        verify_station=verify_station, lose_reply=lose_reply,
    )
    return record_outcome(station, outcome)
