"""Measure fault detection: compare a run's report with the injector's ledger.

A fault counts as detected only if its record carries every expected issue (same category,
same id) traced to the same HL7 field. An issue with the right id on the wrong field is a
miss: the point of the tool is to say *where*.

Everything else a faulted record raises is accounted for, not ignored:
  confirmed  another finding at the same field (e.g. the validator also reporting the missing
             Observation.status that mapping refused to guess): the fault seen twice
  collateral a finding at any other field: the fault spilling onto data it did not touch,
             or a check misfiring. Expected to be zero.

False alarms are counted on the records nobody touched: any error, or any warning the gateway
raises itself (ingest., mapping., link.). The validator's own general warnings (such as a
discouraged UCUM annotation in a unit Synthea uses) are background notes, reported elsewhere
and not counted here.

This module only compares; it never changes a verdict in the report.
"""

from __future__ import annotations

import json
from pathlib import Path

GATEWAY_STAGES = {"ingest", "mapping", "link"}
STAGES = ["ingest", "mapping", "validator", "link"]       # pipeline order


def load_ledger(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def alarm(issue: dict) -> bool:
    """An issue that claims something is wrong with this record."""
    return issue["category"] == "error" or (issue["category"] == "warning" and issue["stage"] in GATEWAY_STAGES)


def in_feed(record: dict, ledger: dict) -> bool:
    """Whether a record came from the faulted feed (a run may also include other inputs)."""
    # Reports show a path relative to the working directory, or only the file name for inputs
    # outside it (pipeline._display_path). A full path must sit in the faulted feed's own
    # folder (ledger "root"), so a clean copy of the same feed in the same run never matches.
    parts = Path(record.get("source_path") or record.get("source_file", "")).parts
    root = ledger.get("root")
    for f in ledger["files"]:
        rel = Path(f).parts
        if len(parts) > len(rel):
            if parts[-len(rel):] == rel and (root is None or parts[-len(rel) - 1] == root):
                return True
        elif parts == rel or (len(parts) == 1 and parts[0] == rel[-1]):
            return True
    return False


def _passing_by_type(ledger: dict, by_id: dict) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in ledger["faults"]:
        rec = by_id.get(f["record_id"])
        if rec is not None and rec["status"] == "pass":
            out[f["fault"]] = out.get(f["fault"], 0) + 1
    return out


def score(records: list[dict], ledger: dict) -> dict:
    # Other inputs in the same run (e.g. the hand-written fixtures) are counted, never scored.
    others = [r for r in records if r["kind"] in ("patient", "lab-report") and not in_feed(r, ledger)]
    records = [r for r in records if in_feed(r, ledger)]
    by_id = {r["record_id"]: r for r in records}
    if len(by_id) != len(records):
        raise ValueError("two records from the faulted feed share a record id; scoring would be ambiguous")
    faulted = {f["record_id"] for f in ledger["faults"]}
    types = {t["name"]: {"name": t["name"], "applies_to": t["applies_to"], "what": t["what"],
                         "expect": t["expect"], "injected": 0, "detected": 0, "missed": [],
                         "caught_by": dict.fromkeys(STAGES, 0), "confirmed_by": dict.fromkeys(STAGES, 0),
                         "collateral": 0}
             for t in ledger["types"]}
    outcomes = []
    for f in ledger["faults"]:
        t = types[f["fault"]]
        t["injected"] += 1
        rec = by_id.get(f["record_id"])
        issues = rec["issues"] if rec else []
        expected = {(e["category"], e["id"], e["at"]) for e in f["expect"]}
        found = {(i["category"], i["message_id"], i.get("source_locator", "")) for i in issues}
        missing = [e for e in f["expect"] if (e["category"], e["id"], e["at"]) not in found]
        caught, confirmed, collateral = set(), set(), []
        for i in issues:
            if not alarm(i):
                continue
            if (i["category"], i["message_id"], i.get("source_locator", "")) in expected:
                caught.add(i["stage"])
            elif i.get("source_locator", "") == f["at"]:
                confirmed.add(i["stage"])
            else:
                collateral.append({"id": i["message_id"], "at": i.get("source_locator", ""), "stage": i["stage"]})
        for stage in caught:
            t["caught_by"][stage] += 1
        for stage in confirmed - caught:
            t["confirmed_by"][stage] += 1
        t["collateral"] += len(collateral)
        if rec is not None and not missing:
            t["detected"] += 1
        else:
            t["missed"].append({"record_id": f["record_id"], "at": f["at"],
                                "reason": "record not in report" if rec is None else
                                          "missing " + ", ".join(f"{e['id']} at {e['at']}" for e in missing)})
        outcomes.append({"record_id": f["record_id"], "fault": f["fault"], "at": f["at"],
                         "before": f.get("before", ""), "after": f.get("after", ""),
                         "detected": rec is not None and not missing,
                         "caught_by": [st for st in STAGES if st in caught],
                         "confirmed_by": [st for st in STAGES if st in confirmed - caught],
                         "collateral": collateral})

    clean = [r for r in records if r["record_id"] not in faulted and r["kind"] in ("patient", "lab-report")]
    false_alarms = [r for r in clean if any(alarm(i) for i in r["issues"])]
    in_faulted = [r for r in records if r["record_id"] in faulted]
    injected = sum(t["injected"] for t in types.values())
    detected = sum(t["detected"] for t in types.values())
    return {
        "ledger": {"seed": ledger["seed"], "per_type": ledger["per_type"], "source_feed": ledger.get("source_feed")},
        "injected": injected,
        "detected": detected,
        "stages": STAGES,
        "types": list(types.values()),
        "collateral_findings": sum(t["collateral"] for t in types.values()),
        "faults": outcomes,
        "clean_records": {"total": len(clean), "passed": sum(r["status"] == "pass" for r in clean),
                          "false_alarms": len(false_alarms),
                          "false_alarm_examples": [
                              {"record_id": r["record_id"],
                               "issues": sorted({i["message_id"] for i in r["issues"] if alarm(i)})}
                              for r in false_alarms[:20]]},
        "other_records": {"total": len(others), "passed": sum(r["status"] == "pass" for r in others)},
        "faulted_records": {"total": len(in_faulted), "passed": sum(r["status"] == "pass" for r in in_faulted),
                            # which kinds of fault left their record passing (warning-only ones should)
                            "passing_by_type": _passing_by_type(ledger, by_id)},
    }
