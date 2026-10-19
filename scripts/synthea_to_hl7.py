"""Synthea CSV -> Australian-style HL7 v2.4 feed (ADT^A04 + ORU^R01), plus a truth file.

Synthea produces realistic but American patients. This exporter:
  - localises each patient: synthetic IHI, Medicare number and MRN (format-valid, check digits
    correct), a Queensland suburb and postcode, Synthea's numeric name suffixes removed;
  - writes one ADT^A04 (registration) per patient;
  - writes one ORU^R01 (lab results) per Synthea encounter that has laboratory observations,
    one OBX per result, LOINC codes and UCUM units exactly as Synthea generated them;
  - writes truth.json: what each patient's data should look like after conversion, so the
    gateway's output can be checked against the source (a round-trip check).

Deliberately NOT done:
  - Units are not converted (e.g. glucose stays mg/dL, the US convention; Australia reports
    mmol/L). Converting would change clinical values, and this project never alters data.
  - No reference ranges or abnormal flags: Synthea does not generate them, and inventing them
    would put made-up clinical judgements into the data.
  - Death details are not sent (Phase 2 maps registration and results only).

Every localised value derives from a hash of the Synthea patient id and the seed, so a patient
always gets the same IHI however many others are generated or in what order.

Usage:  python scripts/synthea_to_hl7.py --csv <synthea output/csv> --out data/synthea
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gateway.identifiers.au import make_ihi, make_medicare  # noqa: E402
from gateway.ingest.hl7v2 import Encoding  # noqa: E402

ENC = Encoding()
BRISBANE = timezone(timedelta(hours=10))   # Queensland has no daylight saving
REGISTERED_AT = "20261019093000+1000"

# Real Queensland suburbs with their real postcodes (public information), used as fictional
# home suburbs for synthetic people.
QLD_SUBURBS = [
    ("BRISBANE CITY", "4000"), ("NEW FARM", "4005"), ("FORTITUDE VALLEY", "4006"),
    ("CHERMSIDE", "4032"), ("TOOWONG", "4066"), ("INDOOROOPILLY", "4068"), ("WEST END", "4101"),
    ("WOOLLOONGABBA", "4102"), ("LOGAN CENTRAL", "4114"), ("CARINDALE", "4152"),
    ("SPRINGFIELD", "4300"), ("IPSWICH", "4305"), ("TOOWOOMBA CITY", "4350"),
    ("SOUTHPORT", "4215"), ("SURFERS PARADISE", "4217"), ("COOLANGATTA", "4225"),
    ("ROBINA", "4226"), ("CALOUNDRA", "4551"), ("MAROOCHYDORE", "4558"), ("NAMBOUR", "4560"),
    ("BUNDABERG CENTRAL", "4670"), ("GLADSTONE CENTRAL", "4680"), ("ROCKHAMPTON CITY", "4700"),
    ("MACKAY", "4740"), ("TOWNSVILLE CITY", "4810"), ("CAIRNS CITY", "4870"),
]

SUITE = re.compile(r"^(.*?)\s+(?:Suite|Unit|Apt\.?)\s+(\w+)$", re.IGNORECASE)


def clean_name(part: str) -> str:
    """Synthea appends digits to names ('Carlo647') to keep them unique."""
    return re.sub(r"\d+$", "", part).strip().upper()


def au_street(address: str) -> str:
    """'353 Wintheiser Tunnel Suite 8' -> '8/353 WINTHEISER TUNNEL' (Australian unit style)."""
    m = SUITE.match(address.strip())
    street = f"{m.group(2)}/{m.group(1)}" if m else address.strip()
    return street.upper()


def hl7_ts(iso: str) -> str:
    """Synthea UTC timestamp -> HL7 TS in Brisbane time."""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(BRISBANE)
    return dt.strftime("%Y%m%d%H%M%S+1000")


def patient_rng(seed: int, synthea_id: str) -> random.Random:
    digest = hashlib.sha256(f"{seed}:{synthea_id}".encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


def localise(row: dict, seed: int) -> dict:
    rng = patient_rng(seed, row["Id"])
    suburb, postcode = rng.choice(QLD_SUBURBS)
    return {
        "synthea_id": row["Id"],
        "ihi": make_ihi(rng),
        "medicare": make_medicare(rng),
        "mrn": f"DL{rng.randint(1_000_000, 9_999_999)}",
        "family": clean_name(row["LAST"]),
        "given": [g for g in (clean_name(row["FIRST"]), clean_name(row["MIDDLE"])) if g],
        "birth_date": row["BIRTHDATE"],                   # already YYYY-MM-DD
        "sex": row["GENDER"],                             # Synthea uses M / F
        "street": au_street(row["ADDRESS"]),
        "suburb": suburb, "postcode": postcode,
    }


def pid(p: dict) -> str:
    e = ENC.escape
    ids = (f"{p['ihi']}^^^AUSHIC^NI~{p['medicare']}^^^AUSHIC^MC~{p['mrn']}^^^DEMOLAB^MR")
    given = "^".join([e(p["given"][0]) if p["given"] else "",
                      e(p["given"][1]) if len(p["given"]) > 1 else ""])
    return (f"PID|1||{ids}||{e(p['family'])}^{given}^^^^L||{p['birth_date'].replace('-', '')}|"
            f"{p['sex']}|||{e(p['street'])}^^{p['suburb']}^QLD^{p['postcode']}^AU^H")


def msh(message_type: str, control_id: str, when: str) -> str:
    return f"MSH|^~\\&|DEMOLAB|QLDDEMO|GATEWAY|AUCORE|{when}||{message_type}|{control_id}|P|2.4"


def adt_a04(p: dict, control_id: str) -> str:
    return "\r".join([msh("ADT^A04^ADT_A01", control_id, REGISTERED_AT),
                      f"EVN|A04|{REGISTERED_AT}", pid(p), "PV1|1|O"]) + "\r"


def oru_r01(p: dict, control_id: str, report_no: str, rows: list[dict]) -> str:
    e = ENC.escape
    when = hl7_ts(min(r["DATE"] for r in rows))
    # OBR-4 uses a sender-local code (coding system "L"): Synthea does not say which panel
    # was ordered, and inventing a LOINC panel code would put a false claim in the data.
    segments = [msh("ORU^R01^ORU_R01", control_id, when), pid(p),
                f"OBR|1|{report_no}|{report_no}|LAB^Pathology request^L|||{when}"
                + "|" * 18 + "F"]
    for n, r in enumerate(rows, start=1):
        numeric = r["TYPE"] == "numeric"
        # Units exactly as Synthea wrote them, {nominal} included (a UCUM annotation for a
        # unitless value): dropping it would change what the source said.
        units = f"{e(r['UNITS'])}^^UCUM" if r["UNITS"] else ""
        segments.append(
            f"OBX|{n}|{'NM' if numeric else 'ST'}|{e(r['CODE'])}^{e(r['DESCRIPTION'])}^LN||"
            f"{e(r['VALUE'])}|{units}|||||F|||{hl7_ts(r['DATE'])}")
    return "\r".join(segments) + "\r"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--csv", type=Path, required=True, help="Synthea output/csv folder")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=20261019)
    ap.add_argument("--patients-per-file", type=int, default=100)
    args = ap.parse_args()

    patients = sorted(csv.DictReader((args.csv / "patients.csv").open(newline="")),
                      key=lambda r: r["Id"])
    labs: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for r in csv.DictReader((args.csv / "observations.csv").open(newline="")):
        if r["CATEGORY"] == "laboratory":
            labs[r["PATIENT"]][r["ENCOUNTER"]].append(r)

    for sub in ("adt", "oru"):
        (args.out / sub).mkdir(parents=True, exist_ok=True)
        for old in (args.out / sub).glob("*.hl7"):
            old.unlink()

    truth, adt_seq, oru_seq = {}, 0, 0
    for start in range(0, len(patients), args.patients_per_file):
        chunk = patients[start:start + args.patients_per_file]
        file_no = start // args.patients_per_file + 1
        adt_msgs, oru_msgs = [], []
        for row in chunk:
            p = localise(row, args.seed)
            adt_seq += 1
            adt_msgs.append(adt_a04(p, f"A{adt_seq:07d}"))
            encounters = sorted(labs[row["Id"]].values(), key=lambda rows: min(r["DATE"] for r in rows))
            for rows in encounters:
                oru_seq += 1
                # Full sort key: Synthea can emit the same test twice at the same moment (two
                # LDL results), and the CSV order of such ties is not guaranteed.
                rows = sorted(rows, key=lambda r: (r["DATE"], r["CODE"], r["VALUE"], r["UNITS"]))
                oru_msgs.append(oru_r01(p, f"R{oru_seq:07d}", f"LR{oru_seq:08d}", rows))
            truth[p["ihi"]] = {
                "synthea_id": p["synthea_id"], "family": p["family"], "given": p["given"],
                "birthDate": p["birth_date"], "gender": {"M": "male", "F": "female"}[p["sex"]],
                "lab_reports": len(encounters),
                "lab_results": sum(len(rows) for rows in encounters),
            }
        (args.out / "adt" / f"adt-{file_no:03d}.hl7").write_text("\n".join(adt_msgs), newline="")
        if oru_msgs:
            (args.out / "oru" / f"oru-{file_no:03d}.hl7").write_text("\n".join(oru_msgs), newline="")

    (args.out / "truth.json").write_text(json.dumps(truth, indent=1, sort_keys=True))
    print(f"{len(patients)} patients -> {adt_seq} ADT^A04, {oru_seq} ORU^R01 "
          f"({sum(t['lab_results'] for t in truth.values())} results) in {args.out}")
    print(f"Feed fingerprint: {fingerprint(args.out)}")


def fingerprint(out: Path) -> str:
    """SHA-256 over every generated HL7 file, in name order. Equal fingerprints on two
    machines mean byte-identical feeds."""
    digest = hashlib.sha256()
    for path in sorted((out / "adt").glob("*.hl7")) + sorted((out / "oru").glob("*.hl7")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]
    return 0


if __name__ == "__main__":
    sys.exit(main())
