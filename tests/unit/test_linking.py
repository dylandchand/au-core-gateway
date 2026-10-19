"""Linking lab reports to registered patients (pipeline._link), without the validator."""

from gateway.ingest.hl7v2 import parse_text
from gateway.ingest.lab_adapter import extract_lab_report
from gateway.ingest.patient_adapter import extract_patient, load_rules
from gateway.mapping.patient_builder import build_patient
from gateway.pipeline import _link

RULES = load_rules("demo-lab")
MSH = r"MSH|^~\&|DEMOLAB|QLD|GW|AU|20261012083000+1000||{type}|{ctrl}|P|2.4"
PID_JANE = "PID|1||8003608333647261^^^AUSHIC^NI~22968184811^^^AUSHIC^MC~DL1^^^DEMOLAB^MR||CITIZEN^JANE^^^^^L||19800215|F"
OBX = "OBX|1|NM|4548-4^Hemoglobin A1c^LN||6.1|%^^UCUM|||||F|||20261012083000+1000"


def registered(pid: str = PID_JANE):
    msg = parse_text("\r".join([MSH.format(type="ADT^A04^ADT_A01", ctrl="A1"), pid]), file="adt.hl7")[0]
    rec, _ = extract_patient(msg, "", RULES)
    patient, _, _ = build_patient(rec, RULES)
    return {"record_id": "adt-A1", "issues": []}, patient


def lab(pid: str):
    msg = parse_text("\r".join([MSH.format(type="ORU^R01^ORU_R01", ctrl="R1"), pid, OBX]), file="oru.hl7")[0]
    report, _ = extract_lab_report(msg, RULES)
    return {"record_id": "oru-R1", "issues": []}, report


def link(pid: str) -> dict:
    entry, report = lab(pid)
    _link([registered()], [(entry, report)])
    return entry


def codes(entry: dict) -> list[tuple[str, str, str]]:
    return [(i["category"], i["message_id"], i["source_locator"]) for i in entry["issues"]]


def test_links_by_ihi():
    entry = link(PID_JANE)
    assert (entry["linked_patient"], entry["linked_by"], codes(entry)) == ("adt-A1", "ihi", [])


def test_falls_back_to_mrn_when_ihi_is_absent():
    entry = link(PID_JANE.replace("8003608333647261^^^AUSHIC^NI~", ""))
    assert (entry["linked_patient"], entry["linked_by"]) == ("adt-A1", "mrn")


def test_ihi_beats_a_conflicting_mrn():
    entry = link(PID_JANE.replace("DL1^^^DEMOLAB^MR", "DL999^^^DEMOLAB^MR"))
    assert entry["linked_by"] == "ihi"


def test_unknown_patient_is_an_orphan_traced_to_pid3():
    entry = link("PID|1||8003608833357361^^^AUSHIC^NI||NOBODY^NEW^^^^^L||19700101|M")
    assert entry["linked_patient"] is None
    assert codes(entry) == [("error", "link.orphan-result", "PID-3")]


def test_same_ihi_different_name_or_birth_date_is_flagged():
    entry = link(PID_JANE.replace("CITIZEN^JANE", "SMITH^JANE").replace("19800215", "19800216"))
    assert entry["linked_patient"] == "adt-A1"
    assert codes(entry) == [("warning", "link.demographic-mismatch", "PID-5[1].1"),
                            ("warning", "link.demographic-mismatch", "PID-7")]


def test_name_case_differences_are_not_flagged():
    assert codes(link(PID_JANE.replace("CITIZEN^JANE", "Citizen^Jane"))) == []


def test_values_missing_from_the_registration_are_not_mismatches():
    # The registration's missing birth date is reported on the Patient; the lab report is fine.
    entry, report = lab(PID_JANE)
    _link([registered(PID_JANE.replace("||19800215|F", "|||F"))], [(entry, report)])
    assert entry["linked_patient"] == "adt-A1" and codes(entry) == []


def test_record_ids_stay_unique_without_or_with_repeated_control_ids():
    from pathlib import Path
    from gateway.pipeline import _record_id
    text = "\n".join("\r".join([MSH.format(type="ORU^R01^ORU_R01", ctrl=c), PID_JANE, OBX]) for c in ("", "", "R1", "R1"))
    seen, got = set(), []
    for msg in parse_text(text, file="oru.hl7"):
        rid, issue = _record_id(Path("oru.hl7"), msg, seen)
        got.append((rid, issue.code if issue else None))
    assert len({rid for rid, _ in got}) == 4
    assert [code for _, code in got] == ["ingest.missing-control-id", "ingest.missing-control-id",
                                          None, "ingest.duplicate-control-id"]
