from enum import StrEnum

from measurement.decode import DecodedReading
from measurement.policy import ShiftPolicy


class QualityFailure(StrEnum):
    BAD_QUALITY = 'bad_quality'
    MISSING_COUNT = 'missing_count'
    NULL_COUNT = 'null_count'


def inspect_quality(reading: DecodedReading, policy: ShiftPolicy) -> QualityFailure | None:
    if reading.quality & policy.bad_quality_mask:
        return QualityFailure.BAD_QUALITY
    if not reading.count_present:
        return QualityFailure.MISSING_COUNT
    if reading.count is None:
        return QualityFailure.NULL_COUNT
    return None
