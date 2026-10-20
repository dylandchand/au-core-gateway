"""Checks the built-in examples in a run against fixtures/expectations.yaml.

The rule is the integration tests' rule, so the dashboard can say the same thing CI proves:
a broken example must report exactly its expected errors, each at its expected HL7 field and
nothing else, with the status that follows from them, plus any expected warnings; every
record in a clean example must pass. Records from other inputs are counted elsewhere and
never scored here.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import yaml

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
EXPECTATIONS = FIXTURES / "expectations.yaml"
GROUPS = ("golden", "faults")


def load(path: Path = EXPECTATIONS) -> dict:
    return yaml.safe_load(Path(path).read_text())


def group_of(file: Path, fixtures: Path) -> str | None:
    """'golden' or 'faults' when the file sits directly in that fixtures folder."""
    parent = file.resolve().parent
    for group in GROUPS:
        if parent == (fixtures / group).resolve():
            return group
    return None


def _errors(record: dict) -> set[tuple[str, str]]:
    return {(i["message_id"], i.get("source_locator", "")) for i in record["issues"] if i["category"] == "error"}


def _warnings(record: dict) -> set[tuple[str, str]]:
    return {(i["message_id"], i.get("source_locator", "")) for i in record["issues"] if i["category"] == "warning"}


def _pairs(items: list[dict]) -> set[tuple[str, str]]:
    return {(e["id"], e["at"]) for e in items or []}


def _as_list(pairs, category: str) -> list[dict]:
    return [{"category": category, "id": i, "at": a} for i, a in sorted(pairs)]


def score(records: list[dict], expect: dict, paths: dict[str, tuple[str, str]]) -> dict | None:
    """paths: report source_path -> (group, file name) for each input that is a built-in example.
    None when the run has no built-in examples."""
    if not paths:
        return None
    by_path: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        if r["source_path"] in paths:
            by_path[r["source_path"]].append(r)

    clean = {"files": 0, "records": 0, "passed": 0, "expected_records": 0, "failed": []}
    items = []
    for source_path, (group, name) in sorted(paths.items(), key=lambda kv: kv[1]):
        recs = by_path.get(source_path, [])
        if group == "golden":
            spec = (expect.get("golden") or {}).get(name) or {}
            clean["files"] += 1
            clean["records"] += len(recs)
            clean["expected_records"] += spec.get("records", len(recs))
            clean["passed"] += sum(r["status"] == "pass" for r in recs)
            clean["failed"] += [r["record_id"] for r in recs if r["status"] != "pass"]
            continue
        spec = (expect.get("faults") or {}).get(name)
        if spec is None:
            items.append({"file": name, "record_id": recs[0]["record_id"] if recs else None, "why": "",
                          "expect": [], "as_expected": False, "missing": [], "unexpected": [],
                          "reason": "no expectation recorded for this file"})
            continue
        expected = _pairs(spec.get("errors"))
        expected_w = _pairs(spec.get("warnings"))
        if len(recs) != 1:
            items.append({"file": name, "record_id": recs[0]["record_id"] if recs else None,
                          "why": " ".join(spec.get("why", "").split()),
                          "expect": _as_list(expected, "error") + _as_list(expected_w, "warning"),
                          "as_expected": False, "missing": [], "unexpected": [],
                          "reason": f"expected one record, found {len(recs)}"})
            continue
        r = recs[0]
        got = _errors(r)
        missing = _as_list(expected - got, "error") + _as_list(expected_w - _warnings(r), "warning")
        unexpected = _as_list(got - expected, "error")
        status_ok = r["status"] == ("fail" if expected else "pass")
        items.append({"file": name, "record_id": r["record_id"], "why": " ".join(spec.get("why", "").split()),
                      "expect": _as_list(expected, "error") + _as_list(expected_w, "warning"),
                      "as_expected": not missing and not unexpected and status_ok,
                      "missing": missing, "unexpected": unexpected,
                      "reason": "" if status_ok else f"record {r['status']}ed"})
    return {"clean": clean,
            "broken": {"files": len(items), "as_expected": sum(i["as_expected"] for i in items), "items": items}}
