"""Encoding for an opaque live read-out packet.

The packet body names the capture announcement, the read-out slot generation and
the exact bytes read.  Authentication stays in the transport port; this module
only defines the authenticated body layout.
"""
import base64
import binascii
import json
from dataclasses import dataclass

from recorder.original import CaptureOrigin


@dataclass(frozen=True, slots=True)
class LivePacket:
    capture_id: str
    batch_id: str
    source: str
    slot_id: str
    generation: int
    digest: str
    raw: bytes


def encode_live_packet(origin: CaptureOrigin, raw: bytes, slot_id: str, generation: int) -> bytes:
    return json.dumps({
        'capture_id': origin.capture_id,
        'batch_id': origin.batch_id,
        'source': origin.source,
        'slot_id': slot_id,
        'generation': generation,
        'digest': origin.digest,
        'raw': base64.b64encode(raw).decode('ascii'),
    }, sort_keys=True, separators=(',', ':')).encode('utf-8')


def decode_live_packet(body: bytes) -> LivePacket:
    if not isinstance(body, (bytes, bytearray)):
        raise ValueError('packet body is not bytes')
    try:
        payload = json.loads(bytes(body).decode('utf-8'))
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise ValueError('packet body is not a valid object') from exc
    if not isinstance(payload, dict):
        raise ValueError('packet body is not a valid object')

    capture_id = payload.get('capture_id')
    batch_id = payload.get('batch_id')
    source = payload.get('source')
    slot_id = payload.get('slot_id')
    generation = payload.get('generation')
    digest = payload.get('digest')
    encoded_raw = payload.get('raw')
    if not all(isinstance(value, str) and value for value in (capture_id, batch_id, source, slot_id, digest)):
        raise ValueError('packet fields are missing')
    if type(generation) is not int or generation <= 0:
        raise ValueError('packet generation is invalid')
    if not isinstance(encoded_raw, str):
        raise ValueError('packet raw is missing')
    try:
        raw = base64.b64decode(encoded_raw.encode('ascii'), validate=True)
    except (ValueError, binascii.Error, UnicodeEncodeError) as exc:
        raise ValueError('packet raw is not valid base64') from exc
    return LivePacket(capture_id, batch_id, source, slot_id, generation, digest, raw)
