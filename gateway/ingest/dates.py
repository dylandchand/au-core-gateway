"""HL7 v2 date/time (TS/DTM) -> ISO 8601, without inventing precision.

HL7: YYYY[MM[DD[HH[MM[SS[.S+]]]]]][+/-ZZZZ]
FHIR date:      YYYY, YYYY-MM or YYYY-MM-DD
FHIR dateTime:  as date, or a full time with seconds AND a timezone
                (FHIR R4: "If hours and minutes are specified, a time zone SHALL be populated")

Rules:
  - partial dates stay partial ('1980' never becomes '1980-01-01');
  - a time with no timezone cannot be made a valid FHIR dateTime without guessing the zone,
    so it is reduced to its date and the caller is told (never silently assumed);
  - minutes without seconds get ':00', which adds no information (same instant).
"""

from __future__ import annotations

import re
from datetime import date, datetime

HL7_TS = re.compile(r"^(\d{4})(\d{2})?(\d{2})?(?:(\d{2})(\d{2})?(\d{2})?(\.\d+)?)?([+-]\d{4})?$")


def hl7_date_to_iso(raw: str) -> str | None:
    """HL7 TS -> FHIR date. Any time part is dropped. None if not a real date."""
    m = HL7_TS.match(raw)
    if not m:
        return None
    year, month, day = m.group(1), m.group(2), m.group(3)
    try:
        if day:
            date(int(year), int(month), int(day))
            return f"{year}-{month}-{day}"
        if month:
            return f"{year}-{month}" if 1 <= int(month) <= 12 else None
        return year
    except ValueError:
        return None


def hl7_ts_to_iso(raw: str) -> tuple[str | None, bool]:
    """HL7 TS -> FHIR dateTime. Returns (value, timezone_missing).
    value is None if the input is not a real date/time."""
    m = HL7_TS.match(raw)
    if not m:
        return None, False
    day_part = hl7_date_to_iso("".join(g for g in m.group(1, 2, 3) if g))
    if day_part is None:
        return None, False
    hh, mi, ss, frac, tz = m.group(4), m.group(5), m.group(6), m.group(7), m.group(8)
    if hh is None:
        return day_part, False
    if len(day_part) != 10 or mi is None:
        return None, False          # a time needs a full date and at least HHMM
    try:
        datetime(*map(int, day_part.split("-")), int(hh), int(mi), int(ss or 0))
    except ValueError:
        return None, False
    if tz is None:
        return day_part, True
    if int(tz[1:3]) > 14 or int(tz[3:5]) > 59:
        return None, False
    return f"{day_part}T{hh}:{mi}:{ss or '00'}{frac or ''}{tz[:3]}:{tz[3:]}", False
