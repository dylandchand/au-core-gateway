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


def pid(p: dict, identifiers: str | None = None) -> str:
    ids = identifiers if identifiers is not None else (
        f"{p['ihi']}^^^AUSHIC^NI~{p['medicare']}^^^AUSHIC^MC~{p['mrn']}^^^DEMOLAB^MR")
    return (f"PID|1||{ids}||{p['family']}^{p['given']}^{p['middle']}^^^^L||{p['dob']}|{p['sex']}|||"
            f"{p['street']}^^{p['suburb']}^QLD^{p['postcode']}^AU^H")


def adt_a04(p: dict, control_id: str, identifiers: str | None = None) -> str:
    return "\r".join([
        f"MSH|^~\\&|DEMOLAB|QLDDEMO|GATEWAY|AUCORE|20261019093000+1000||ADT^A04^ADT_A01|{control_id}|P|2.4",
        "EVN|A04|20261019093000+1000",
        pid(p, identifiers),
        "PV1|1|O",
    ]) + "\r"


# Lab results: (value type, LOINC code, LOINC display, value, UCUM unit, HL7 timestamp).
# Codes and displays as Synthea v3.4.0 generates them.
HBA1C = ("NM", "4548-4", "Hemoglobin A1c/Hemoglobin.total in Blood", "6.1", "%", "20261012083000+1000")
GLUCOSE = ("NM", "2339-0", "Glucose [Mass/volume] in Blood", "92.4", "mg/dL", "20261012083000+1000")
URINE = ("ST", "5767-9", "Appearance of Urine", "Cloudy urine (finding)", "", "20261012083000+1000")


def oru_r01(p: dict, control_id: str, report_no: str, results: list[tuple], identifiers: str | None = None,
            family: str | None = None) -> str:
    q = dict(p, family=family) if family else p
    segments = [
        f"MSH|^~\\&|DEMOLAB|QLDDEMO|GATEWAY|AUCORE|{results[0][5]}||ORU^R01^ORU_R01|{control_id}|P|2.4",
        pid(q, identifiers),
        f"OBR|1|{report_no}|{report_no}|LAB^Pathology request^L|||{results[0][5]}" + "|" * 18 + "F",
    ]
    for n, (vt, code, display, value, unit, ts) in enumerate(results, start=1):
        units = f"{unit}^^UCUM" if unit else ""
        segments.append(f"OBX|{n}|{vt}|{code}^{display}^LN||{value}|{units}|||||F|||{ts}")
    return "\r".join(segments) + "\r"


# A second synthetic lab (gateway/mapping/rules/demo-lab-b.yaml): its own sender, its own MRNs
# (CX-4 DEMOLAB2), its own test codes (OBX-3.3 "L"), and PID-3 in another order (MRN first).
# Results: (value type, the lab's own code, the lab's own name, value, UCUM unit, HL7 timestamp).
# Glucose in mg/dL (mass per volume), which is what LOINC 2339-0 means; see demo-lab-b.yaml.
B_GLUCOSE = ("NM", "GLU", "Glucose, blood", "88", "mg/dL", "20261014101500+1000")
B_HBA1C = ("NM", "HBA1C", "HbA1c", "6.4", "%", "20261014101500+1000")
B_URINE = ("ST", "UAPP", "Urine appearance", "Clear", "", "20261014101500+1000")


def oru_r01_lab_b(p: dict, control_id: str, report_no: str, results: list[tuple],
                  identifiers: str | None = None, sender: str = "DEMOLAB2|QLDDEMO2") -> str:
    ids = identifiers if identifiers is not None else (
        f"{p['mrn_b']}^^^DEMOLAB2^MR~{p['ihi']}^^^AUSHIC^NI~{p['medicare']}^^^AUSHIC^MC")
    segments = [
        f"MSH|^~\\&|{sender}|GATEWAY|AUCORE|{results[0][5]}||ORU^R01^ORU_R01|{control_id}|P|2.4",
        pid(p, ids),
        f"OBR|1|{report_no}|{report_no}|PATH^Pathology request^L|||{results[0][5]}" + "|" * 18 + "F",
    ]
    for n, (vt, code, display, value, unit, ts) in enumerate(results, start=1):
        units = f"{unit}^^UCUM" if unit else ""
        segments.append(f"OBX|{n}|{vt}|{code}^{display}^L||{value}|{units}|||||F|||{ts}")
    return "\r".join(segments) + "\r"


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
    people = [person(rng) for _ in range(5)]
    (golden / "clean-patients.hl7").write_text(
        "\n".join(adt_a04(p, f"GOLD{n:04d}") for n, p in enumerate(people, start=1)), newline="")

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

    # Phase 2: lab results (ORU^R01). Golden reports belong to golden patients; each lab fault
    # file breaks exactly one thing in a report that is otherwise clean and correctly linked.
    gold1, gold2 = people[0], people[1]
    (golden / "lab-results.hl7").write_text("\n".join([
        oru_r01(gold1, "GOLDLAB0001", "LR90000001", [HBA1C, GLUCOSE]),
        oru_r01(gold2, "GOLDLAB0002", "LR90000002", [URINE]),
    ]), newline="")

    def lab_fault(name: str, p: dict, results: list[tuple], replace: tuple[str, str] | None = None, **kw) -> None:
        text = oru_r01(p, f"FAULT{next(counter):04d}", f"LR9{next(counter):07d}", results, **kw)
        if replace:
            assert text.count(replace[0]) == 1, replace
            text = text.replace(*replace)
        (faults / f"{name}.hl7").write_text(text, newline="")

    stranger = person(rng)   # registered nowhere
    lab_fault("lab-orphan-result", stranger, [HBA1C])
    lab_fault("lab-non-numeric-value", gold1, [GLUCOSE, HBA1C[:3] + ("6,1",) + HBA1C[4:]])
    lab_fault("lab-impossible-datetime", gold1, [GLUCOSE[:5] + ("20261332083000+1000",)])
    lab_fault("lab-name-mismatch", gold1, [HBA1C], family="SOMEBODYELSE")

    # Phase 3: results become AU Core Observations. Each file below breaks one thing that only
    # matters once a result is converted.
    lab_fault("obs-unmapped-status", gold2, [HBA1C], replace=("|||||F|||", "|||||Z|||"))
    lab_fault("obs-unknown-coding-system", gold2, [HBA1C], replace=("^LN||", "^XX||"))

    # Phase 4: a LOINC code whose check digit is wrong (4548-4 written as 4548-5).
    lab_fault("lab-bad-loinc-check-digit", gold2, [HBA1C], replace=("|4548-4^", "|4548-5^"))

    # Phase 4: the remaining shapes the scale fault injector uses. Added last so the random
    # sequence, and so every fixture above, is unchanged.
    p = person(rng)
    fault("ihi-unknown-authority", p, identifiers=(f"{p['ihi']}^^^AUSHC^NI~{p['medicare']}^^^AUSHIC^MC"
                                                   f"~{p['mrn']}^^^DEMOLAB^MR"))
    p = person(rng); p["dob"] = f"{p['dob'][6:8]}/{p['dob'][4:6]}/{p['dob'][0:4]}"; fault("birth-date-day-first", p)

    # A result value with two parts (repeat separator): reported, never cut to the first part.
    lab_fault("lab-multipart-value", gold2, [HBA1C], replace=("||6.1|%", "||5.6~6.1|%"))

    # The second lab. Added last so every fixture above is unchanged. Its reports are about
    # patients the first lab registered, so they must link across senders: by IHI, or by the
    # first lab's own MRN when the second lab quotes it, never by a number another lab issued.
    gold3, gold4 = people[2], people[3]
    for q in (gold1, gold3):
        q["mrn_b"] = f"QB{rng.randint(100000, 999999)}"
    (golden / "lab-b-results.hl7").write_text("\n".join([
        # The same control IDs as the first lab's golden reports: a different sender, so not resends.
        oru_r01_lab_b(gold3, "GOLDLAB0001", "QB80000001", [B_GLUCOSE, B_HBA1C]),
        oru_r01_lab_b(gold4, "GOLDLAB0002", "QB80000002", [B_URINE],
                      identifiers=f"{gold4['mrn']}^^^DEMOLAB^MR"),
    ]), newline="")

    def lab_b_fault(name: str, p: dict, results: list[tuple], **kw) -> None:
        text = oru_r01_lab_b(p, f"FAULT{next(counter):04d}", f"QB8{next(counter):07d}", results, **kw)
        (faults / f"{name}.hl7").write_text(text, newline="")

    lab_b_fault("lab-b-unmapped-local-code", gold3, [("NM", "CRP", "C-reactive protein", "3.2", "mg/L",
                                                       B_GLUCOSE[5])])
    # Only an MRN the second lab issued, which is the same number the first lab issued to a
    # different patient: matching on the number alone would file these results under that person.
    lab_b_fault("lab-b-mrn-other-issuer", gold3, [B_GLUCOSE],
                identifiers=f"{gold1['mrn']}^^^DEMOLAB2^MR")
    lab_b_fault("lab-b-time-no-timezone", gold3, [B_GLUCOSE[:5] + ("202610141015",)])
    lab_b_fault("unknown-sender", gold3, [B_GLUCOSE], sender="OTHERLAB|QLDOTHER")

    print(f"Wrote {len(list(golden.glob('*.hl7')))} golden and {len(list(faults.glob('*.hl7')))} fault fixtures")
    return 0


if __name__ == "__main__":
    sys.exit(main())
