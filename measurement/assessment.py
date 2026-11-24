from dataclasses import dataclass

from measurement.decode import InvalidReading, decode_reading
from measurement.policy import ShiftPolicy
from measurement.quality import inspect_quality
from measurement.units import to_micrometers


@dataclass(frozen=True, slots=True)
class MeasurementResult:
    micrometers: int | None
    reason: str | None


def assess_original(raw: bytes, policy: ShiftPolicy) -> MeasurementResult:
    try:
        reading = decode_reading(raw)
    except InvalidReading as exc:
        return MeasurementResult(None, str(exc))
    failure = inspect_quality(reading, policy)
    if failure is not None:
        return MeasurementResult(None, failure.value)
    value = to_micrometers(reading.count, policy.zero_offset, policy.micrometers_per_count)
    return MeasurementResult(value, None)
