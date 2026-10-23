"""Break a known share of an HL7 v2 feed in known ways, and record every break in a ledger.

Clean data mostly passes, which proves little. This takes a generated feed (from
synthea_to_hl7.py), copies it, and corrupts N messages per fault type. Each message gets at
most one fault. Every fault is written to faults.json with the record it hit, what changed,
and the issue the gateway must raise for it (id and HL7 field). Detection is then measured
against that ledger (gateway/faults.py), not eyeballed.

Same feed, same seed, same --per-type: byte-identical output and ledger.

The expected issues per fault type are the same ids the hand-written fault fixtures prove in
fixtures/expectations.yaml, so the two can't drift apart silently (tests/unit/test_faults.py
checks this).

Usage:
  python scripts/inject_faults.py data/synthea-1000 data/synthea-1000-faulted [--per-type 25]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gateway.identifiers.au import make_ihi, make_medicare  # noqa: E402
from gateway.identifiers.loinc import FORMAT as LOINC_FORMAT  # noqa: E402

SEED = 20261019
AU_IHI = "http://hl7.org.au/fhir/StructureDefinition/au-ihi#"
DIFFERENT_FAMILY = "SOMEBODYELSE"       # never a Synthea surname (they are mixed-case words)


class Skip(Exception):
    """This message has nothing the fault can break (e.g. no numeric result)."""


# ---------------------------------------------------------------- HL7 text helpers
# The feed comes from our own exporter: no escape sequences in the fields touched here.

def seg_fields(msg: list[str], name: str, occurrence: int = 1) -> tuple[int, list[str]]:
    seen = 0
    for i, seg in enumerate(msg):
        if seg.startswith(name + "|"):
            seen += 1
            if seen == occurrence:
                return i, seg.split("|")
    raise Skip(f"no {name}({occurrence})")


def put(msg: list[str], index: int, fields: list[str]) -> None:
    msg[index] = "|".join(fields)


def obx_count(msg: list[str]) -> int:
    return sum(seg.startswith("OBX|") for seg in msg)


@dataclass
class Change:
    at: str                 # HL7 locator the expected issue must point at
    before: str
    after: str


# ---------------------------------------------------------------- the faults
# Each takes the message (a list of segments, edited in place) and a seeded RNG.

def ihi_check_digit(msg, rng):
    i, f = seg_fields(msg, "PID")
    reps = f[3].split("~")
    value, *rest = reps[0].split("^")
    if not value.startswith("800360") or "^".join(rest) != "^^AUSHIC^NI":
        raise Skip("first identifier is not an IHI")
    broken = value[:-1] + str((int(value[-1]) + rng.randint(1, 9)) % 10)
    reps[0] = "^".join([broken, *rest])
    f[3] = "~".join(reps)
    put(msg, i, f)
    return Change("PID-3[1]", value, broken)


def ihi_authority_typo(msg, rng):
    i, f = seg_fields(msg, "PID")
    reps = f[3].split("~")
    if not reps[0].endswith("^^^AUSHIC^NI"):
        raise Skip("first identifier is not an IHI")
    reps[0] = reps[0].replace("^^^AUSHIC^NI", "^^^AUSHC^NI")
    f[3] = "~".join(reps)
    put(msg, i, f)
    return Change("PID-3[1]", "AUSHIC", "AUSHC")


def missing_birth_date(msg, rng):
    i, f = seg_fields(msg, "PID")
    if not f[7]:
        raise Skip("no birth date to remove")
    before, f[7] = f[7], ""
    put(msg, i, f)
    return Change("PID-7", before, "")


def birth_date_day_first(msg, rng):
    i, f = seg_fields(msg, "PID")
    if len(f[7]) != 8:
        raise Skip("birth date is not YYYYMMDD")
    before = f[7]
    f[7] = f"{before[6:8]}/{before[4:6]}/{before[0:4]}"
    put(msg, i, f)
    return Change("PID-7", before, f[7])


def orphan_result(msg, rng):
    i, f = seg_fields(msg, "PID")
    # A person registered nowhere: fresh synthetic identifiers (IHIs are 16 random-ish digits,
    # so a collision with the registry is checked by the caller).
    before = f[3]
    f[3] = (f"{make_ihi(rng)}^^^AUSHIC^NI~{make_medicare(rng)}^^^AUSHIC^MC"
            f"~ZZ{rng.randint(100000, 999999)}^^^DEMOLAB^MR")
    put(msg, i, f)
    return Change("PID-3", before, f[3])


def _pick_obx(msg, rng, ok: Callable[[list[str]], bool]) -> tuple[int, int, list[str]]:
    candidates = []
    for n in range(1, obx_count(msg) + 1):
        i, f = seg_fields(msg, "OBX", n)
        if len(f) > 14 and ok(f):
            candidates.append((n, i, f))
    if not candidates:
        raise Skip("no suitable OBX")
    return rng.choice(candidates)


def bad_loinc_check_digit(msg, rng):
    n, i, f = _pick_obx(msg, rng, lambda f: f[3].split("^")[2:3] == ["LN"]
                        and LOINC_FORMAT.match(f[3].split("^")[0]) is not None)
    comps = f[3].split("^")
    before = comps[0]
    comps[0] = before[:-1] + str((int(before[-1]) + rng.randint(1, 9)) % 10)
    f[3] = "^".join(comps)
    put(msg, i, f)
    return Change(f"OBX({n})-3", before, comps[0])


def non_numeric_value(msg, rng):
    n, i, f = _pick_obx(msg, rng, lambda f: f[2] == "NM" and "." in f[5])
    before = f[5]
    f[5] = before.replace(".", ",")
    put(msg, i, f)
    return Change(f"OBX({n})-5", before, f[5])


def impossible_result_time(msg, rng):
    n, i, f = _pick_obx(msg, rng, lambda f: len(f[14]) >= 8)
    before = f[14]
    f[14] = before[:4] + "13" + before[6:]
    put(msg, i, f)
    return Change(f"OBX({n})-14", before, f[14])


def unmapped_status(msg, rng):
    n, i, f = _pick_obx(msg, rng, lambda f: f[11] == "F")
    f[11] = "Z"
    put(msg, i, f)
    return Change(f"OBX({n})-11", "F", "Z")


def name_mismatch(msg, rng):
    i, f = seg_fields(msg, "PID")
    comps = f[5].split("^")
    before = comps[0]
    comps[0] = DIFFERENT_FAMILY
    f[5] = "^".join(comps)
    put(msg, i, f)
    return Change("PID-5[1].1", before, DIFFERENT_FAMILY)


@dataclass
class FaultType:
    name: str
    applies_to: str                     # "adt" or "oru"
    what: str
    expect: list[tuple[str, str]]       # (category, issue id); the field comes from the Change
    break_it: Callable


VAL_MIN = "Validation_VAL_Profile_Minimum"
CATALOGUE = [
    FaultType("ihi-check-digit", "adt", "last digit of the IHI changed (Luhn check fails)",
              [("error", AU_IHI + "inv-ihi-value-2")], ihi_check_digit),
    FaultType("ihi-authority-typo", "adt", "IHI assigning authority misspelt (AUSHIC as AUSHC)",
              [("warning", "ingest.unmapped-identifier")], ihi_authority_typo),
    FaultType("missing-birth-date", "adt", "birth date removed",
              [("error", VAL_MIN)], missing_birth_date),
    FaultType("birth-date-day-first", "adt", "birth date written DD/MM/YYYY",
              [("error", "ingest.bad-date"), ("error", VAL_MIN)], birth_date_day_first),
    FaultType("orphan-result", "oru", "lab report sent for a patient who was never registered",
              [("error", "link.orphan-result")], orphan_result),
    FaultType("bad-loinc-check-digit", "oru", "one result's LOINC check digit changed",
              [("error", "ingest.bad-loinc-code")], bad_loinc_check_digit),
    FaultType("non-numeric-value", "oru", "one numeric result written with a decimal comma",
              [("error", "ingest.bad-numeric")], non_numeric_value),
    FaultType("impossible-result-time", "oru", "one result time set to month 13",
              [("error", "ingest.bad-datetime")], impossible_result_time),
    FaultType("unmapped-status", "oru", "one result status F changed to Z",
              [("error", "mapping.unmapped-code")], unmapped_status),
    FaultType("name-mismatch", "oru", "family name differs from the registration",
              [("warning", "link.demographic-mismatch")], name_mismatch),
]


# ---------------------------------------------------------------- feed handling

def load_feed(feed: Path) -> dict[Path, list[list[str]]]:
    """relative path -> messages, each a list of segments (as the exporter wrote them)."""
    out = {}
    for path in sorted(feed.rglob("*.hl7")):
        with open(path, encoding="utf-8", newline="") as fh:     # keep \r: it separates segments
            text = fh.read()
        out[path.relative_to(feed)] = [m.split("\r") for m in text.split("\n") if m]
    return out


def write_feed(dest: Path, files: dict[Path, list[list[str]]]) -> None:
    for rel, messages in files.items():
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        (dest / rel).write_text("".join("\r".join(m) + "\n" for m in messages), encoding="utf-8",
                                newline="")


def control_id(msg: list[str]) -> str:
    return msg[0].split("|")[9]


def registered_identifiers(files) -> set[str]:
    ids = set()
    for rel, messages in files.items():
        for m in messages:
            for seg in m:
                if seg.startswith("PID|"):
                    ids.update(rep.split("^")[0] for rep in seg.split("|")[3].split("~"))
    return ids


def inject(feed: Path, dest: Path, per_type: int, seed: int = SEED) -> dict:
    files = load_feed(feed)
    known_ids = registered_identifiers(files)
    rng = random.Random(seed)
    pools = {"adt": [], "oru": []}
    for rel, messages in files.items():
        for k, m in enumerate(messages):
            kind = {"ADT": "adt", "ORU": "oru"}.get(m[0].split("|")[8][:3])
            if kind:
                pools[kind].append((rel, k))
    for pool in pools.values():
        rng.shuffle(pool)

    taken: set[tuple[Path, int]] = set()
    faults = []
    for ft in CATALOGUE:
        done = 0
        for rel, k in pools[ft.applies_to]:
            if done == per_type:
                break
            if (rel, k) in taken:
                continue
            msg = list(files[rel][k])
            try:
                change = ft.break_it(msg, rng)
            except Skip:
                continue
            if ft.name == "orphan-result" and {r.split("^")[0] for r in change.after.split("~")} & known_ids:
                continue
            files[rel][k] = msg
            taken.add((rel, k))
            done += 1
            faults.append({
                "fault": ft.name,
                "record_id": f"{rel.stem}-{control_id(msg)}",
                "file": rel.as_posix(),
                "at": change.at, "before": change.before, "after": change.after,
                "expect": [{"category": c, "id": i, "at": change.at} for c, i in ft.expect],
            })
        if done < per_type:
            raise SystemExit(f"only {done} messages could take fault {ft.name!r} (wanted {per_type})")

    if dest.exists():
        shutil.rmtree(dest)
    write_feed(dest, files)
    totals = {kind: len(pool) for kind, pool in pools.items()}
    ledger = {
        "seed": seed, "per_type": per_type, "source_feed": feed.name,
        "root": dest.name,          # the faulted feed's folder: a clean copy never matches
        "files": [rel.as_posix() for rel in files],
        "messages": totals,
        "faulted_messages": len(faults),
        "types": [{"name": ft.name, "applies_to": ft.applies_to, "what": ft.what,
                   "expect": [{"category": c, "id": i} for c, i in ft.expect]} for ft in CATALOGUE],
        "faults": faults,
    }
    (dest / "faults.json").write_text(json.dumps(ledger, indent=1) + "\n")
    # The faulted copy is still the same synthetic population: keep the note saying what made it.
    if (feed / "source.json").is_file():
        shutil.copyfile(feed / "source.json", dest / "source.json")
    return ledger


def fingerprint(dest: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(dest.rglob("*")):
        if path.is_file():
            h.update(path.relative_to(dest).as_posix().encode())
            h.update(path.read_bytes())
    return h.hexdigest()[:16]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("feed", type=Path, help="feed directory (with adt/ and oru/)")
    ap.add_argument("dest", type=Path, help="output directory (replaced)")
    ap.add_argument("--per-type", type=int, default=25, help="messages broken per fault type")
    ap.add_argument("--seed", type=int, default=SEED)
    a = ap.parse_args()
    ledger = inject(a.feed, a.dest, a.per_type, a.seed)
    total = sum(ledger["messages"].values())
    print(f"{ledger['faulted_messages']} of {total} messages faulted "
          f"({len(CATALOGUE)} types x {a.per_type}) -> {a.dest}")
    print(f"Faulted feed fingerprint: {fingerprint(a.dest)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
