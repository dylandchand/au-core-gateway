"""Fault injector (scripts/inject_faults.py) and detection scoring (gateway/faults.py)."""

import json
from pathlib import Path

import yaml

from gateway import faults
from gateway.ingest.hl7v2 import parse_file
from tests.fault_feed import ROOT, load_script, small_feed

inject = load_script("inject_faults")
EXPECT = yaml.safe_load((ROOT / "fixtures" / "expectations.yaml").read_text())["faults"]

# Each scale fault type and the hand-written fixture that proves the same detection.
FIXTURE_FOR = {
    "ihi-check-digit": "ihi-bad-check-digit.hl7",
    "ihi-authority-typo": "ihi-unknown-authority.hl7",
    "missing-birth-date": "missing-birth-date.hl7",
    "birth-date-day-first": "birth-date-day-first.hl7",
    "orphan-result": "lab-orphan-result.hl7",
    "bad-loinc-check-digit": "lab-bad-loinc-check-digit.hl7",
    "non-numeric-value": "lab-non-numeric-value.hl7",
    "impossible-result-time": "lab-impossible-datetime.hl7",
    "unmapped-status": "obs-unmapped-status.hl7",
    "name-mismatch": "lab-name-mismatch.hl7",
}


def test_every_fault_type_expects_what_its_fixture_proves():
    assert {ft.name for ft in inject.CATALOGUE} == set(FIXTURE_FOR)
    for ft in inject.CATALOGUE:
        spec = EXPECT[FIXTURE_FOR[ft.name]]
        proven = {("error", e["id"]) for e in spec["errors"]} | {("warning", w["id"]) for w in spec.get("warnings", [])}
        assert set(ft.expect) <= proven, ft.name


def test_injected_faults_point_at_the_same_field_their_fixture_proves(tmp_path):
    import re
    norm = lambda at: re.sub(r"\(\d+\)", "", at)        # OBX(3)-5 and OBX(1)-5 are the same field
    _, _, ledger = run_injector(tmp_path)
    for f in ledger["faults"]:
        spec = EXPECT[FIXTURE_FOR[f["fault"]]]
        proven = {(e["id"], norm(e["at"])) for e in spec["errors"] + spec.get("warnings", [])}
        for e in f["expect"]:
            assert (e["id"], norm(e["at"])) in proven, (f["fault"], e)


def run_injector(tmp_path, per_type=1):
    feed = small_feed(tmp_path / "feed")
    ledger = inject.inject(feed, tmp_path / "faulted", per_type)
    return feed, tmp_path / "faulted", ledger


def test_injection_is_deterministic_and_one_fault_per_message(tmp_path):
    _, out, ledger = run_injector(tmp_path)
    first = inject.fingerprint(out)
    assert len(ledger["faults"]) == len(inject.CATALOGUE)
    assert len({f["record_id"] for f in ledger["faults"]}) == len(ledger["faults"])
    inject.inject(tmp_path / "feed", out, 1)
    assert inject.fingerprint(out) == first


# Where each fault's change lands: (segment, field, repeat, component); None = the whole field.
CHANGED_AT = {
    "ihi-check-digit": ("PID", 3, 1, 1), "ihi-authority-typo": ("PID", 3, 1, 4),
    "missing-birth-date": ("PID", 7, None, None), "birth-date-day-first": ("PID", 7, None, None),
    "orphan-result": ("PID", 3, None, None), "bad-loinc-check-digit": ("OBX", 3, 1, 1),
    "non-numeric-value": ("OBX", 5, None, None), "impossible-result-time": ("OBX", 14, None, None),
    "unmapped-status": ("OBX", 11, None, None), "name-mismatch": ("PID", 5, 1, 1),
}


def test_each_change_is_exactly_where_the_ledger_says(tmp_path):
    _, out, ledger = run_injector(tmp_path)
    assert set(CHANGED_AT) == {ft.name for ft in inject.CATALOGUE}
    messages = {f"{p.stem}-{m.control_id}": m for p in out.rglob("*.hl7") for m in parse_file(p)}
    for f in ledger["faults"]:
        seg_name, field, rep, comp = CHANGED_AT[f["fault"]]
        msg = messages[f["record_id"]]
        occ = int(f["at"].split("(")[1].split(")")[0]) if "(" in f["at"] else 1
        seg = msg.all(seg_name)[occ - 1]
        actual = seg.field(field) if rep is None else seg.value(field, rep, comp)
        assert actual == f["after"] and f["before"] != f["after"], (f["fault"], actual, f)
        assert f["at"].startswith(seg_name) and f"-{field}" in f["at"], f


def test_untouched_messages_are_byte_identical(tmp_path):
    feed, out, ledger = run_injector(tmp_path)
    hit = {f["record_id"] for f in ledger["faults"]}
    before, after = inject.load_feed(feed), inject.load_feed(out)
    assert before.keys() == after.keys()
    for rel in before:
        assert len(before[rel]) == len(after[rel])
        for b, a in zip(before[rel], after[rel]):
            if f"{rel.stem}-{inject.control_id(b)}" not in hit:
                assert a == b


def record(rid, issues=(), kind="lab-report", path="data/faulted/oru/oru-001.hl7"):
    issues = list(issues)
    return {"record_id": rid, "kind": kind, "source_path": path, "issues": issues,
            "status": "fail" if any(i["category"] == "error" for i in issues) else "pass"}


def issue(mid, at, category="error", stage="ingest"):
    return {"message_id": mid, "source_locator": at, "category": category, "stage": stage}


LEDGER = {"seed": 1, "per_type": 1, "files": ["oru/oru-001.hl7"],
          "types": [{"name": "non-numeric-value", "applies_to": "oru", "what": "x",
                     "expect": [{"category": "error", "id": "ingest.bad-numeric"}]}],
          "faults": [{"fault": "non-numeric-value", "record_id": "r1", "at": "OBX(2)-5",
                      "expect": [{"category": "error", "id": "ingest.bad-numeric", "at": "OBX(2)-5"}]},
                     {"fault": "non-numeric-value", "record_id": "r2", "at": "OBX(1)-5",
                      "expect": [{"category": "error", "id": "ingest.bad-numeric", "at": "OBX(1)-5"}]}]}


def test_detection_needs_the_right_id_at_the_right_field():
    s = faults.score([record("r1", [issue("ingest.bad-numeric", "OBX(2)-5")]),
                      record("r2", [issue("ingest.bad-numeric", "OBX(3)-5")]),   # wrong field: a miss
                      record("r3")], LEDGER)
    assert (s["injected"], s["detected"]) == (2, 1)
    assert s["types"][0]["missed"][0]["record_id"] == "r2"
    assert s["clean_records"] == {"total": 1, "passed": 1, "false_alarms": 0, "false_alarm_examples": []}


def test_false_alarms_count_errors_and_gateway_warnings_on_untouched_records_only():
    s = faults.score([record("r1", [issue("ingest.bad-numeric", "OBX(2)-5")]),
                      record("r3", [issue("UCUM_ANNOTATIONS", "OBX(1)-6", "warning", "validator")]),
                      record("r4", [issue("link.demographic-mismatch", "PID-7", "warning", "link")]),
                      record("other", [issue("x", "PID-3")], path="fixtures/faults/x.hl7")], LEDGER)
    c = s["clean_records"]
    assert (c["total"], c["false_alarms"]) == (2, 1)        # r3 is a background note; "other" is outside the feed
    assert c["false_alarm_examples"] == [{"record_id": "r4", "issues": ["link.demographic-mismatch"]}]


def test_other_inputs_in_the_run_are_counted_but_never_scored():
    s = faults.score([record("r1", [issue("ingest.bad-numeric", "OBX(2)-5")]),
                      record("ok", path="fixtures/golden/ok.hl7"),
                      record("bad", [issue("x", "PID-3")], path="fixtures/faults/bad.hl7")], LEDGER)
    assert s["other_records"] == {"total": 2, "passed": 1}
    assert s["clean_records"]["total"] == 0


def test_records_outside_the_working_directory_are_matched_by_file_name():
    assert faults.in_feed({"source_path": "oru-001.hl7"}, LEDGER)
    assert faults.in_feed({"source_path": "data/x/oru/oru-001.hl7"}, LEDGER)
    assert not faults.in_feed({"source_path": "data/x/adt/oru-001.hl7"}, LEDGER)


def test_other_findings_on_a_faulted_record_are_confirmations_or_collateral():
    s = faults.score([record("r1", [issue("ingest.bad-numeric", "OBX(2)-5"),
                                    issue("Validation_X", "OBX(2)-5", stage="validator"),     # same field
                                    issue("Validation_Y", "OBX(4)-5", stage="validator")]),   # elsewhere
                      record("r2", [issue("ingest.bad-numeric", "OBX(1)-5")])], LEDGER)
    t = s["types"][0]
    assert t["caught_by"]["ingest"] == 2 and t["confirmed_by"]["validator"] == 1
    assert t["collateral"] == s["collateral_findings"] == 1
    r1 = next(f for f in s["faults"] if f["record_id"] == "r1")
    assert r1["caught_by"] == ["ingest"] and r1["confirmed_by"] == ["validator"]
    assert r1["collateral"] == [{"id": "Validation_Y", "at": "OBX(4)-5", "stage": "validator"}]


def test_a_clean_copy_of_the_feed_is_never_scored():
    ledger = LEDGER | {"root": "faulted"}
    assert faults.in_feed({"source_path": "data/faulted/oru/oru-001.hl7"}, ledger)
    assert not faults.in_feed({"source_path": "data/synthea-1000/oru/oru-001.hl7"}, ledger)


def test_duplicate_record_ids_in_the_feed_are_refused():
    import pytest
    with pytest.raises(ValueError, match="share a record id"):
        faults.score([record("r1"), record("r1")], LEDGER)


def test_passing_faulted_records_are_listed_by_kind():
    s = faults.score([record("r1"), record("r2", [issue("ingest.bad-numeric", "OBX(1)-5")])], LEDGER)
    assert s["faulted_records"]["passing_by_type"] == {"non-numeric-value": 1}
