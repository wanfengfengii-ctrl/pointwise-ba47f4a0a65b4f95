from decimal import Decimal


def to_micrometers(count: int, zero_offset: int, micrometers_per_count: int) -> int:
    return (count - zero_offset) * micrometers_per_count


def encode_integer(value: int) -> str:
    # Decimal construction is exact and keeps large integers out of SQLite's 64-bit range.
    return str(Decimal(value))


def decode_integer(value: str) -> int:
    return int(Decimal(value))
