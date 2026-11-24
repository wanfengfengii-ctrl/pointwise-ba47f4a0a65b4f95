import json
from dataclasses import dataclass

from measurement.units import decode_integer


@dataclass(frozen=True, slots=True)
class DecodedReading:
    count: int | None
    quality: int
    count_present: bool


class InvalidReading(ValueError):
    pass


def _reject_constant(token: str) -> None:
    raise ValueError(f'invalid JSON constant {token}')


def decode_reading(raw: bytes) -> DecodedReading:
    try:
        payload = json.loads(raw.decode('utf-8'), parse_int=decode_integer,
                             parse_constant=_reject_constant)
    except (ValueError, RecursionError) as exc:
        raise InvalidReading('invalid_payload') from exc
    if not isinstance(payload, dict):
        raise InvalidReading('invalid_payload')
    quality = payload.get('quality')
    if type(quality) is not int or quality < 0:
        raise InvalidReading('invalid_quality')
    count = payload.get('count')
    if count is not None and type(count) is not int:
        raise InvalidReading('invalid_count')
    return DecodedReading(count, quality, 'count' in payload)
