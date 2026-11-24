import sqlite3

from intake.evidence import announce_origin, bind_preservation, read_preservation
from intake.operations import bind_operation, read_operation
from intake.receipts import CaptureReceipt, find_receipt, receive_original
from intake.resume import resume_release
from recorder.capture import read_live_original, read_origin
from recorder.release import ReleaseOutcome
from recorder.retained import read_retained_original, retain_original


def adopt(
    station: sqlite3.Connection, device: sqlite3.Connection, capture_id: str,
    operation_id: str, *, method: str = 'station', lose_reply: bool = False,
) -> ReleaseOutcome:
    if method not in ('station', 'device'):
        raise ValueError('unknown preservation path')
    if not isinstance(operation_id, str) or not operation_id:
        raise ValueError('operation number must be a nonempty string')
    previous = read_operation(station, operation_id)
    if previous is not None and previous.origin.capture_id != capture_id:
        raise ValueError('station release operation already bound')
    origin = read_origin(device, capture_id)
    if origin is None:
        raise KeyError(capture_id)
    announce_origin(station, origin)
    saved = read_preservation(station, capture_id)
    if saved is not None and saved.method != method:
        raise ValueError('capture already bound to another preservation path')

    if saved is None:
        if method == 'device':
            retained = retain_original(device, capture_id)
            bind_preservation(station, retained.original, method, retained.reference)
        else:
            receipt = find_receipt(station, capture_id)
            if receipt is None:
                original = read_live_original(device, capture_id)
                if original is None:
                    raise LookupError(f'live original unavailable for {capture_id}')
                receipt = receive_original(station, original)
            bind_preservation(station, receipt.original, method, capture_id)

    bind_operation(station, operation_id, origin)
    outcome = resume_release(station, device, operation_id, retry=True, lose_reply=lose_reply)
    if outcome is None:
        raise RuntimeError('release remains pending')
    return outcome


def receive_retained(
    station: sqlite3.Connection, device: sqlite3.Connection, capture_id: str,
) -> CaptureReceipt:
    saved = read_retained_original(device, capture_id)
    if saved is None:
        raise LookupError(f'no fixed original for {capture_id}')
    return receive_original(station, saved.original)
