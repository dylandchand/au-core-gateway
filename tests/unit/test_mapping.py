"""Adapter (HL7 v2 -> canonical) and builder (canonical -> AU Core Patient), no validator."""

import pytest

from gateway.identifiers.au import IHI_SYSTEM, MEDICARE_SYSTEM
from gateway.ingest.hl7v2 import parse_text
from gateway.ingest.patient_adapter import extract_patient, hl7_date_to_fhir, load_rules
from gateway.mapping.patient_builder import AU_CORE_PATIENT, build_patient, locate

RULES = load_rules("demo-lab")


def message(pid: str) -> str:
    return "\r".join([r"MSH|^~\&|DEMOLAB|QLD|GW|AU|20261019||ADT^A04^ADT_A01|C1|P|2.4", pid])


CLEAN_PID = ("PID|1||8003608333647261^^^AUSHIC^NI~22968184811^^^AUSHIC^MC~DL1^^^DEMOLAB^MR||"
             "CITIZEN^JANE^MARY^^^^L||19800215|F|||12 SMITH ST^^BRISBANE^QLD^4000^AU^H")


def convert(pid: str):
    msg = parse_text(message(pid), file="t.hl7")[0]
    rec, ingest_issues = extract_patient(msg, "demo-lab", RULES)
    patient, trace, map_issues = build_patient(rec, RULES)
    return patient, trace, ingest_issues + map_issues


@pytest.mark.parametrize("raw,expected", [
    ("19800215", "1980-02-15"), ("198002", "1980-02"), ("1980", "1980"),
    ("198002151230", "1980-02-15"), ("19800231", None), ("198013", None), ("15/02/1980", None),
])
def test_hl7_dates(raw, expected):
    assert hl7_date_to_fhir(raw) == expected


def test_clean_patient_shape():
    patient, _, issues = convert(CLEAN_PID)
    assert issues == []
    assert patient["meta"]["profile"] == [AU_CORE_PATIENT]
    ihi, medicare, mrn = patient["identifier"]
    assert (ihi["system"], ihi["type"]["coding"][0]["code"]) == (IHI_SYSTEM, "NI")
    assert (medicare["system"], medicare["type"]["coding"][0]["code"]) == (MEDICARE_SYSTEM, "MC")
    assert mrn["type"]["coding"][0]["code"] == "MR" and mrn["assigner"]["display"]
    assert "example" not in mrn["system"]
    assert patient["name"] == [{"use": "official", "family": "CITIZEN", "given": ["JANE", "MARY"]}]
    assert (patient["gender"], patient["birthDate"]) == ("female", "1980-02-15")
    assert patient["address"][0] == {"use": "home", "line": ["12 SMITH ST"], "city": "BRISBANE",
                                     "state": "QLD", "postalCode": "4000", "country": "AU"}
    assert patient["text"]["status"] == "generated"


def test_bad_values_are_passed_through_not_fixed():
    """The builder must not repair data: the validator has to see the problem."""
    patient, _, _ = convert(CLEAN_PID.replace("8003608333647261", "8003608333647262"))
    assert patient["identifier"][0]["value"] == "8003608333647262"


def test_partial_birth_date_stays_partial():
    patient, _, _ = convert(CLEAN_PID.replace("19800215", "1980"))
    assert patient["birthDate"] == "1980"


def test_impossible_date_is_an_ingest_error_and_is_not_emitted():
    patient, trace, issues = convert(CLEAN_PID.replace("19800215", "19800231"))
    assert [i.code for i in issues] == ["ingest.bad-date"]
    assert "birthDate" not in patient
    assert trace["Patient.birthDate"].locator == "PID-7"


def test_unknown_sex_code_is_flagged_not_guessed():
    patient, _, issues = convert(CLEAN_PID.replace("|F|", "|X|"))
    assert "gender" not in patient
    assert [i.code for i in issues] == ["mapping.unmapped-code"]


def test_unknown_identifier_type_is_skipped_with_a_warning():
    patient, _, issues = convert(CLEAN_PID.replace("~DL1^^^DEMOLAB^MR", "~X9^^^OTHER^ZZ"))
    assert len(patient["identifier"]) == 2
    assert [i.code for i in issues] == ["ingest.unmapped-identifier"]


def test_trace_points_every_element_at_its_field():
    _, trace, _ = convert(CLEAN_PID)
    assert trace["Patient.identifier[0]"].locator == "PID-3[1]"
    assert trace["Patient.identifier[2]"].locator == "PID-3[3]"
    assert trace["Patient.name[0].given[1]"].locator == "PID-5[1].3"
    assert trace["Patient.address[0].state"].locator == "PID-11[1].4"
    assert trace["Patient.gender"].locator == "PID-8"
    assert trace["Patient.birthDate"].line == 2


@pytest.mark.parametrize("expression,message,message_id,expected", [
    ("Patient.identifier[0]", "", "", "PID-3[1]"),
    ("Patient.identifier[0].value", "", "", "PID-3[1]"),
    ("Patient.address[0].state", "", "", "PID-11[1].4"),
    ("Patient", "Patient.birthDate: minimum required = 1, but only found 0", "", "PID-7"),
    ("Patient", "Constraint failed", "http://x#au-core-pat-01", "PID-3"),
    ("Patient", "something else", "", "PID"),
])
def test_locate_maps_validator_expressions_to_source(expression, message, message_id, expected):
    _, trace, _ = convert(CLEAN_PID)
    assert locate(trace, expression, message, message_id).locator == expected
