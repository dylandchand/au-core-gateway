"""Two senders: rules chosen by MSH-3/MSH-4, MRNs matched only within their issuer, a sender's own
test codes translated to LOINC only by its rules file, and resends told apart by sender."""

import pytest
import yaml

from gateway import pipeline
from gateway.identifiers.loinc import loinc_problem
from gateway.ingest import routing
from gateway.ingest.hl7v2 import parse_text
from gateway.ingest.patient_adapter import extract_patient
from gateway.interface.session import Session
from gateway.mapping.patient_builder import build_patient

A_MRN = "http://ns.electronichealth.net.au/id/hpio-scoped/medicalrecord/1.0/8003628946342779"
B_MRN = "http://ns.electronichealth.net.au/id/hpio-scoped/medicalrecord/1.0/8003620457180938"
LOINC = "http://loinc.org"

ADT = r"MSH|^~\&|{app}|{fac}|GW|AU|20261019093000+1000||ADT^A04^ADT_A01|{ctrl}|P|2.4"
ORU = r"MSH|^~\&|{app}|{fac}|GW|AU|20261014101500+1000||ORU^R01^ORU_R01|{ctrl}|P|2.4"
PID = "PID|1||{ids}||CITIZEN^JANE^^^^^L||19800215|F|||1 MAIN ST^^BRISBANE^QLD^4000^AU^H"
JANE = "8003608333647261^^^AUSHIC^NI~22968184811^^^AUSHIC^MC~DL104522^^^DEMOLAB^MR"
OBR = "OBR|1|R1|R1|PATH^Pathology request^L|||20261014101500+1000"
OBX_GLU = "OBX|1|NM|GLU^Glucose, blood^L||88|mg/dL^^UCUM|||||F|||20261014101500+1000"


def adt(ids=JANE, app="DEMOLAB", fac="QLDDEMO", ctrl="A1"):
    return "\r".join([ADT.format(app=app, fac=fac, ctrl=ctrl), PID.format(ids=ids), "PV1|1|O"]) + "\r"


def oru(ids, app="DEMOLAB2", fac="QLDDEMO2", ctrl="R1", obx=OBX_GLU):
    return "\r".join([ORU.format(app=app, fac=fac, ctrl=ctrl), PID.format(ids=ids), OBR, obx]) + "\r"


def run(tmp_path, *messages) -> dict:
    feed = tmp_path / "feed"
    feed.mkdir()
    for n, text in enumerate(messages):
        (feed / f"m{n}.hl7").write_text(text, newline="")
    return pipeline.run([feed], routing.AUTO, tmp_path / "out", validate=False)


def lab_record(report):
    return next(r for r in report["records"] if r["kind"] == "lab-report")


def ids(record):
    return [(i["category"], i["message_id"], i["source_locator"]) for i in record["issues"]]


# ---- routing -------------------------------------------------------------------------------

def test_each_rules_file_names_one_sender():
    assert routing.senders() == {("DEMOLAB", "QLDDEMO"): "demo-lab", ("DEMOLAB2", "QLDDEMO2"): "demo-lab-b"}


def test_two_rules_files_claiming_one_sender_are_refused(tmp_path, monkeypatch):
    for name in ("one", "two"):
        (tmp_path / f"{name}.yaml").write_text(yaml.safe_dump({"sender": {"application": "X", "facility": "Y"}}))
    monkeypatch.setattr(routing, "RULES_DIR", tmp_path)
    with pytest.raises(ValueError, match="both claim sender"):
        routing.senders()


def test_messages_are_read_with_their_own_senders_rules(tmp_path):
    report = run(tmp_path, adt(), oru(JANE))
    assert report["source_rules"] == "demo-lab, demo-lab-b (chosen per message by sender)"
    assert lab_record(report)["status"] == "pass"


def test_an_unknown_sender_is_reported_and_not_converted(tmp_path):
    report = run(tmp_path, adt(), oru(JANE, app="OTHERLAB", fac="QLDOTHER"))
    rec = lab_record(report)
    assert ids(rec) == [("error", "ingest.unknown-sender", "MSH-3")]
    assert "OTHERLAB" in rec["issues"][0]["message"] and not rec.get("resources")


def test_a_forced_rules_file_still_applies_to_every_message(tmp_path):
    feed = tmp_path / "feed"
    feed.mkdir()
    (feed / "a.hl7").write_text(adt(app="ANY", fac="WHERE"), newline="")
    report = pipeline.run([feed], "demo-lab", tmp_path / "out", validate=False)
    assert report["source_rules"] == "demo-lab" and report["records"][0]["status"] == "pass"


# ---- MRNs belong to their issuer -------------------------------------------------------------

def test_the_same_mrn_number_from_another_issuer_does_not_link(tmp_path):
    # The second lab issued DL104522 to someone; the first lab issued the same number to Jane.
    rec = lab_record(run(tmp_path, adt(), oru("DL104522^^^DEMOLAB2^MR")))
    assert ids(rec) == [("error", "link.orphan-result", "PID-3")]


def test_the_issuers_own_mrn_quoted_by_another_sender_links(tmp_path):
    rec = lab_record(run(tmp_path, adt(), oru("DL104522^^^DEMOLAB^MR")))
    assert (rec["linked_by"], ids(rec)) == ("mrn", [])


def test_national_numbers_link_across_senders(tmp_path):
    rec = lab_record(run(tmp_path, adt(), oru("QB777777^^^DEMOLAB2^MR~8003608333647261^^^AUSHIC^NI")))
    assert (rec["linked_by"], ids(rec)) == ("ihi", [])


def test_an_mrn_keeps_the_system_of_the_rule_that_matched_it():
    rules = routing.load_rules("demo-lab-b")
    msg = parse_text(adt("B1^^^DEMOLAB2^MR~A1^^^DEMOLAB^MR", app="DEMOLAB2", fac="QLDDEMO2"), file="b.hl7")[0]
    rec, _ = extract_patient(msg, "demo-lab-b", rules)
    patient, _, _ = build_patient(rec, rules)
    got = [(i["value"], i["system"], i["assigner"]["display"]) for i in patient["identifier"]]
    assert got == [("B1", B_MRN, "Second Demo Pathology Lab (synthetic)"),
                   ("A1", A_MRN, "Demo Pathology Queensland (synthetic)")]


def test_the_first_lab_takes_only_its_own_mrns(tmp_path):
    report = run(tmp_path, adt("8003608333647261^^^AUSHIC^NI~QB1^^^DEMOLAB2^MR"))
    [rec] = report["records"]
    assert ("warning", "ingest.unmapped-identifier", "PID-3[2]") in ids(rec)


# ---- the sender's own test codes -----------------------------------------------------------

def test_a_local_code_is_translated_to_loinc_and_keeps_the_senders_name(tmp_path):
    out = tmp_path / "out"
    run(tmp_path, adt(), oru(JANE))
    [obs] = [yaml.safe_load(p.read_text()) for p in (out / "resources").glob("*.json")
             if '"Observation"' in p.read_text()]
    assert obs["code"] == {"coding": [{"system": LOINC, "code": "2339-0",
                                       "display": "Glucose [Mass/volume] in Blood"}],
                           "text": "Glucose, blood"}
    assert obs["performer"] == [{"display": "Second Demo Pathology Lab (synthetic)"}]


def test_a_local_code_missing_from_the_table_is_not_guessed(tmp_path):
    obx = "OBX|1|NM|CRP^C-reactive protein^L||3.2|mg/L^^UCUM|||||F|||20261014101500+1000"
    rec = lab_record(run(tmp_path, adt(), oru(JANE, obx=obx)))
    assert ids(rec) == [("error", "mapping.unmapped-code", "OBX(1)-3")]


@pytest.mark.parametrize("path", sorted(routing.RULES_DIR.glob("*.yaml")), ids=lambda p: p.stem)
def test_every_translation_target_is_a_valid_loinc_code(path):
    fhir = yaml.safe_load(path.read_text())["lab_results"]["fhir"]
    for system, table in fhir.get("local_codes", {}).items():
        for local, target in table.items():
            assert target["system"] in fhir["coding_systems"], (local, target)
            assert fhir["coding_systems"][target["system"]] == LOINC
            assert loinc_problem(target["code"]) is None, (local, target)
            assert target["display"], local


# ---- resends are recognised per sender -------------------------------------------------------

def test_the_same_control_id_from_another_sender_is_not_a_resend(tmp_path):
    s = Session(tmp_path)
    s.receive(adt())
    a = s.receive(oru(JANE, app="DEMOLAB", fac="QLDDEMO", ctrl="SAME1",
                      obx="OBX|1|NM|2339-0^Glucose [Mass/volume] in Blood^LN||88|mg/dL^^UCUM|||||F|||20261014101500+1000"))
    b = s.receive(oru(JANE, ctrl="SAME1"))
    assert (a["code"], a["duplicate_of"]) == ("AA", None)
    assert (b["code"], b["duplicate_of"]) == ("AA", None)
    assert not any(i["message_id"] == "interface.reused-control-id" for i in b["issues"])
