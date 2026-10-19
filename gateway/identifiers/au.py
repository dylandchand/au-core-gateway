"""Australian healthcare identifier formats: validation and synthetic generation.

IHI rules are taken from the AU Base 6.0.0 profile au-ihi (invariants inv-ihi-value-0/1/2):
16 digits, prefix 800360, Luhn check digit. The validator enforces these; we implement them
too so the synthetic data generator only produces valid IHIs, and so fault fixtures can
break exactly one rule at a time.

Medicare card number checks are NOT part of AU Core or AU Base 6.0.0 (the au-medicarecardnumber
profile has no value invariant). They are provided as a gateway-side pre-flight check and for
generating realistic synthetic numbers.

All generated numbers are synthetic. Never use or store real identifiers.
"""

from __future__ import annotations

import random

IHI_PREFIX = "800360"
IHI_SYSTEM = "http://ns.electronichealth.net.au/id/hi/ihi/1.0"
MEDICARE_SYSTEM = "http://ns.electronichealth.net.au/id/medicare-number"
V2_0203 = "http://terminology.hl7.org/CodeSystem/v2-0203"


# --- Luhn / IHI ---------------------------------------------------------------------------

def luhn_check_digit(payload: str) -> str:
    """Check digit that makes payload + digit pass the Luhn algorithm."""
    total = 0
    for i, ch in enumerate(reversed(payload)):
        d = int(ch)
        if i % 2 == 0:  # rightmost payload digit is doubled
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return str((10 - total % 10) % 10)


def luhn_valid(number: str) -> bool:
    return number.isdigit() and len(number) > 1 and luhn_check_digit(number[:-1]) == number[-1]


def ihi_problems(value: str) -> list[str]:
    """Which AU Base IHI rules a value breaks (empty list = valid)."""
    problems = []
    if not (value.isdigit() and len(value) == 16):
        problems.append("inv-ihi-value-0: must be exactly 16 digits")
    if not value.startswith(IHI_PREFIX):
        problems.append("inv-ihi-value-1: must start with 800360")
    if value.isdigit() and len(value) == 16 and not luhn_valid(value):
        problems.append("inv-ihi-value-2: Luhn check digit fails")
    return problems


def make_ihi(rng: random.Random) -> str:
    """A synthetic, format-valid IHI."""
    payload = IHI_PREFIX + "".join(str(rng.randint(0, 9)) for _ in range(9))
    return payload + luhn_check_digit(payload)


# --- Medicare -----------------------------------------------------------------------------

_MEDICARE_WEIGHTS = (1, 3, 7, 9, 1, 3, 7, 9)


def medicare_check_digit(first8: str) -> str:
    return str(sum(int(d) * w for d, w in zip(first8, _MEDICARE_WEIGHTS)) % 10)


def medicare_problems(value: str) -> list[str]:
    """Gateway pre-flight check: 10 digits (card) or 11 digits (card + IRN),
    first digit 2-6, 9th digit is the weighted check digit, IRN 1-9 if present."""
    if not value.isdigit() or len(value) not in (10, 11):
        return ["medicare: must be 10 digits, or 11 with the individual reference number"]
    problems = []
    if value[0] not in "23456":
        problems.append("medicare: first digit must be 2-6")
    if medicare_check_digit(value[:8]) != value[8]:
        problems.append("medicare: check digit (9th digit) fails")
    if len(value) == 11 and value[10] == "0":
        problems.append("medicare: individual reference number must be 1-9")
    return problems


def make_medicare(rng: random.Random, irn: int | None = None) -> str:
    """A synthetic, format-valid Medicare number with issue number and IRN."""
    first8 = str(rng.randint(2, 6)) + "".join(str(rng.randint(0, 9)) for _ in range(7))
    issue = str(rng.randint(1, 9))
    return first8 + medicare_check_digit(first8) + issue + str(irn or rng.randint(1, 5))
