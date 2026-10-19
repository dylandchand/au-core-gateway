"""Results store: saving runs, recomputing headline numbers, and diffing errors between runs."""

import copy

from gateway import store


def report(records, generated="2026-10-19T00:00:00+00:00", inputs=("fixtures/faults",)):
    counts = {c: sum(i["category"] == c for r in records for i in r["issues"])
              for c in ("error", "could-not-check", "warning", "info")}
    return {"generated": generated, "inputs": list(inputs), "source_rules": "demo-lab", "validator": "7.0.2",
            "versions": {}, "package_substitutions": [],
            "summary": {"records": len(records), "passed": sum(r["status"] == "pass" for r in records),
                        "failed": sum(r["status"] == "fail" for r in records), **counts},
            "records": records}


def rec(rid, *issues):
    return {"record_id": rid, "kind": "patient", "source_path": "f.hl7", "issues": list(issues),
            "status": "fail" if any(i["category"] == "error" for i in issues) else "pass"}


def iss(mid, at="PID-7", category="error", stage="validator"):
    return {"stage": stage, "category": category, "message_id": mid, "source_locator": at,
            "expression": "Patient.birthDate", "message": "m"}


BASE = [rec("p1", iss("Validation_VAL_Profile_Minimum")),
        rec("p2", iss("CNC", "PID-11", "could-not-check")),
        rec("p3")]


def test_numbers_recomputed_from_the_store_match_the_report(tmp_path):
    db = store.connect(tmp_path / "h.db")
    r = report(BASE)
    run_id = store.save(db, r)
    s = r["summary"]
    assert store.check(db, run_id) == {k: s[k] for k in ("records", "passed", "failed", "error",
                                                          "could-not-check", "warning", "info")}


def test_diff_reports_new_and_resolved_findings_by_field(tmp_path):
    db = store.connect(tmp_path / "h.db")
    first = store.save(db, report(BASE))
    changed = copy.deepcopy(BASE)
    changed[0]["issues"] = [iss("Validation_VAL_Profile_Minimum", at="PID-8")]          # moved field
    changed[2] = rec("p3", iss("ingest.unmapped-identifier", "PID-3[1]", "warning", "ingest"))
    changed[1]["issues"].append(iss("Background", "PID-3[2]", "warning"))                # validator note: ignored
    second = store.save(db, report(changed, generated="2026-10-19T01:00:00+00:00"))
    assert store.previous_run(db, second) == first
    d = store.diff(db, first, second)
    assert [(f["record_id"], f["at"]) for f in d["appeared"]] == [("p1", "PID-8"), ("p3", "PID-3[1]")]
    assert [(f["record_id"], f["at"]) for f in d["resolved"]] == [("p1", "PID-7")]


def test_previous_run_only_matches_the_same_inputs(tmp_path):
    db = store.connect(tmp_path / "h.db")
    a = store.save(db, report(BASE))
    store.save(db, report(BASE, inputs=("data/other",)))
    c = store.save(db, report(BASE))
    assert store.previous_run(db, c) == a
    assert store.history(db)[0]["id"] == c
