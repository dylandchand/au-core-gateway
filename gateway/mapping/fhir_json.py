"""Writing FHIR JSON without losing decimal precision.

FHIR decimals keep their precision: a lab reporting 2.80 is saying something different from
2.8 (FHIR R4, datatypes: "the precision of the decimal value has significance"). Python's json
module would write the float 2.8, silently dropping the trailing zero. So numeric values are
carried as `Decimal` strings and written into the JSON exactly as received.
"""

from __future__ import annotations

import json
import re

_MARK = "\ue000num\ue000"   # private-use characters: JSON leaves them as they are
_RAW = re.compile('"' + re.escape(_MARK) + r'(-?\d+(?:\.\d+)?)"')


class Decimal(str):
    """A number as text, written into JSON as a bare number with its digits unchanged."""


def fhir_decimal(raw: str) -> Decimal:
    """Turn a numeric string from HL7 (already checked as a number) into valid JSON number
    text without changing its value or precision: '+1' -> '1', '.5' -> '0.5', '5.' -> '5',
    '007.10' -> '7.10'."""
    text = raw.strip()
    sign = "-" if text.startswith("-") else ""
    text = text.lstrip("+-")
    whole, _, frac = text.partition(".")
    whole = whole.lstrip("0") or "0"
    return Decimal(f"{sign}{whole}.{frac}" if frac else f"{sign}{whole}")


def dumps(resource: dict, indent: int = 2) -> str:
    def mark(value):
        if isinstance(value, Decimal):
            return _MARK + value
        if isinstance(value, dict):
            return {k: mark(v) for k, v in value.items()}
        if isinstance(value, list):
            return [mark(v) for v in value]
        return value
    return _RAW.sub(r"\1", json.dumps(mark(resource), indent=indent, ensure_ascii=False))
