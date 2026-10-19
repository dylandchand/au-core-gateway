"""LOINC code format and check digit.

A LOINC code is digits, a hyphen and one check digit (e.g. 4548-4). The check digit uses the
Mod 10 algorithm in the LOINC Users' Guide, Appendix C ("Calculating Mod 10 check digits"):
  1-2. number the digits from the right; take the odd positions (1st, 3rd, ...)
  3.   multiply that number by 2
  4-5. take the even positions and prepend them to the result of step 3
  6.   add all those digits together
  7-8. the check digit is the distance to the next multiple of 10
Worked example in the guide: 12345 -> 5.

LOINC notes 14 deprecated codes whose stored check digit is wrong (e.g. 11491-6, which computes
as 8; all 14 are in tests/unit/test_loinc.py). A sender should not use deprecated codes, so failing them is acceptable.
Prefixed LOINC parts and answers (LP, LA, ...) use a different variant and are not handled.
"""

from __future__ import annotations

import re

FORMAT = re.compile(r"^(\d{1,7})-(\d)$")


def mod10(digits: str) -> int:
    rev = digits[::-1]
    odd, even = rev[0::2][::-1], rev[1::2][::-1]
    total = sum(int(c) for c in even + str(int(odd) * 2))
    return (10 - total % 10) % 10


def loinc_problem(code: str) -> str | None:
    """None if code is a well-formed LOINC code with a correct check digit, else why not."""
    m = FORMAT.match(code)
    if not m:
        return f"{code!r} is not in LOINC format (digits, hyphen, check digit)"
    expected = mod10(m.group(1))
    if expected != int(m.group(2)):
        return f"{code!r} fails the LOINC Mod 10 check digit (expected {m.group(1)}-{expected})"
    return None
