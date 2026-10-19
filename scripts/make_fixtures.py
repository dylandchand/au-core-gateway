"""Generate the Phase 1 HL7 v2 fixtures (deterministic: same seed, same files).

golden/  clean, realistic, fully synthetic patients that should pass AU Core with 0 errors
faults/  one file per fault; each breaks exactly ONE thing in an otherwise clean message

All people, identifiers and addresses are synthetic. Identifiers are format-valid
(correct prefixes and check digits) but are not real numbers.

Usage:  python scripts/make_fixtures.py
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gateway.identifiers.au import luhn_check_digit, make_ihi, make_medicare  # noqa: E402

FIXTURES = ROOT / "fixtures"
SEED = 20261019

FAMILY = ["CITIZEN", "NGUYEN", "O'CONNOR", "PATEL", "WILLIAMS", "TAUFA", "MURRAY", "CHEN"]
GIVEN_F = ["JANE", "OLIVIA", "PRIYA", "MELE", "GRACE"]
GIVEN_M = ["JAMES", "LIAM", "ARJUN", "TEVITA", "NOAH"]
MIDDLE = ["MARY", "ROSE", "LEE", "ANN", "JOHN", ""]
STREETS = ["12 SMITH ST", "4/88 BOUNDARY RD", "7 JACARANDA CRES", "151 LOGAN RD", "3 PALM AVE"]
SUBURBS = [("BRISBANE", "4000"), ("WOOLLOONGABBA", "4102"), ("SOUTHPORT", "4215"),
           ("TOWNSVILLE", "4810"), ("TOOWOOMBA", "4350")]


def person(rng: random.Random) -> dict:
    sex = rng.choice("FM")
    suburb, postcode = rng.choice(SUBURBS)
    return {
        "ihi": make_ihi(rng), "medicare": make_medicare(rng),
        "mrn": f"DL{rng.randint(100000, 999999)}",
        "family": rng.choice(FAMILY),
        "given": rng.choice(GIVEN_F if sex == "F" else GIVEN_M),
        "middle": rng.choice(MIDDLE),
        "dob": f"{rng.randint(1940, 2015)}{rng.randint(1, 12):02d}{rng.randint(1, 28):02d}",
        "sex": sex, "street": rng.choice(STREETS), "suburb": suburb, "postcode": postcode,
    }


def adt_a04(p: dict, control_id: str, identifiers: str | None = None) -> str:
    ids = identifiers if identifiers is not None else (
        f"{p['ihi']}^^^AUSHIC^NI~{p['medicare']}^^^AUSHIC^MC~{p['mrn']}^^^DEMOLAB^MR")
    return "\r".join([
        f"MSH|^~\\&|DEMOLAB|QLDDEMO|GATEWAY|AUCORE|20261019093000+1000||ADT^A04^ADT_A01|{control_id}|P|2.4",
        "EVN|A04|20261019093000+1000",
        f"PID|1||{ids}||{p['family']}^{p['given']}^{p['middle']}^^^^L||{p['dob']}|{p['sex']}|||"
        f"{p['street']}^^{p['suburb']}^QLD^{p['postcode']}^AU^H",
        "PV1|1|O",
    ]) + "\r"


def ihi_bad_luhn(ihi: str) -> str:
    return ihi[:-1] + str((int(ihi[-1]) + 1) % 10)


def ihi_wrong_prefix(rng: random.Random) -> str:
    payload = "800361" + "".join(str(rng.randint(0, 9)) for _ in range(9))  # 800361 = HPI-I range
    return payload + luhn_check_digit(payload)


def main() -> int:
    rng = random.Random(SEED)
    golden = FIXTURES / "golden"
    faults = FIXTURES / "faults"
    for d in (golden, faults):
        d.mkdir(parents=True, exist_ok=True)
        for old in d.glob("*.hl7"):
            old.unlink()

    # Golden: five clean patients in one file (several messages per file, as feeds arrive).
    (golden / "clean-patients.hl7").write_text(
        "\n".join(adt_a04(person(rng), f"GOLD{n:04d}") for n in range(1, 6)), newline="")

    # Faults: one file per fault, one message each, everything else clean.
    counter = iter(range(1, 100))

    def fault(name: str, p: dict, identifiers: str | None = None) -> None:
        # MSH-10 (message control id) is limited to 20 characters in HL7 v2.4.
        (faults / f"{name}.hl7").write_text(adt_a04(p, f"FAULT{next(counter):04d}", identifiers), newline="")

    p = person(rng); p["ihi"] = ihi_bad_luhn(p["ihi"]);           fault("ihi-bad-check-digit", p)
    p = person(rng); p["ihi"] = ihi_wrong_prefix(rng);            fault("ihi-wrong-prefix", p)
    p = person(rng); p["dob"] = "";                               fault("missing-birth-date", p)
    p = person(rng); p["dob"] = "19800231";                       fault("impossible-birth-date", p)
    p = person(rng); p["sex"] = "";                               fault("missing-sex", p)
    p = person(rng); p["sex"] = "X";                              fault("unmapped-sex-code", p)
    p = person(rng);                                              fault("no-identifiers", p, identifiers="")

    print(f"Wrote {len(list(golden.glob('*.hl7')))} golden and {len(list(faults.glob('*.hl7')))} fault fixtures")
    return 0


if __name__ == "__main__":
    sys.exit(main())
