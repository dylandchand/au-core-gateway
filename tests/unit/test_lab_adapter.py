from gateway.ingest.hl7v2 import parse_text
from gateway.ingest.lab_adapter import extract_lab_report
from gateway.ingest.patient_adapter import load_rules

RULES = load_rules("demo-lab")
PID = ("PID|1||8003608333647261^^^AUSHIC^NI~DL1^^^DEMOLAB^MR||CITIZEN^JANE^^^^^L||19800215|F")


def oru(*obx: str, obr: bool = True) -> str:
    segs = [r"MSH|^~\&|DEMOLAB|QLD|GW|AU|20261012083000+1000||ORU^R01^ORU_R01|R1|P|2.4", PID]
    if obr:
        segs.append("OBR|1|LR1|LR1|LAB^Pathology request^L|||20261012083000+1000" + "|" * 18 + "F")
    return "\r".join(segs + list(obx))


HBA1C = "OBX|1|NM|4548-4^Hemoglobin A1c^LN||6.1|%^^UCUM|||||F|||20261012083000+1000"
URINE = "OBX|2|ST|5767-9^Appearance of Urine^LN||Cloudy \\S\\ hazy|||||F|||20261012083000+1000"


def extract(text: str):
    return extract_lab_report(parse_text(text, file="lab.hl7")[0], RULES)


def test_results_are_extracted_with_traces():
    report, issues = extract(oru(HBA1C, URINE))
    assert issues == []
    assert [i.kind for i in report.patient_identifiers] == ["ihi", "mrn"]
    assert report.report_id.value == "LR1"
    a1c, urine = report.results
    assert (a1c.code.value, a1c.code_system.value, a1c.value.value, a1c.units.value) == ("4548-4", "LN", "6.1", "%")
    assert a1c.effective.value == "2026-10-12T08:30:00+10:00"
    assert a1c.value.source.locator == "OBX(1)-5"
    assert a1c.code.source.locator == "OBX(1)-3"
    assert a1c.display.source.locator == "OBX(1)-3.2"
    assert urine.value.value == "Cloudy ^ hazy"          # escape sequence decoded
    assert urine.value.source.locator == "OBX(2)-5"


def test_report_keeps_patient_details_for_cross_checking():
    report, _ = extract(oru(HBA1C))
    assert report.patient_family.value == "CITIZEN"
    assert report.patient_birth_date.value == "1980-02-15"


def test_numeric_result_that_is_not_a_number():
    _, issues = extract(oru(HBA1C.replace("||6.1|", "||6,1|")))
    assert [(i.code, i.source.locator) for i in issues] == [("ingest.bad-numeric", "OBX(1)-5")]


def test_text_result_may_hold_any_text():
    _, issues = extract(oru(URINE))
    assert issues == []


def test_impossible_and_zoneless_times():
    _, bad = extract(oru(HBA1C.replace("20261012083000+1000", "20261312083000+1000")))
    assert [(i.code, i.source.locator) for i in bad] == [("ingest.bad-datetime", "OBX(1)-14")]
    report, zoneless = extract(oru(HBA1C.replace("20261012083000+1000", "20261012083000")))
    assert [(i.code, i.severity) for i in zoneless] == [("ingest.datetime-no-timezone", "warning")]
    assert report.results[0].effective.value == "2026-10-12"


def test_missing_code_and_no_results():
    _, issues = extract(oru(HBA1C.replace("4548-4^Hemoglobin A1c^LN", "")))
    assert [i.code for i in issues] == ["ingest.missing-code"]
    _, issues = extract(oru())
    assert [i.code for i in issues] == ["ingest.no-results"]


def test_no_pid_is_an_error():
    msg = r"MSH|^~\&|DEMOLAB|QLD|GW|AU|20261012||ORU^R01^ORU_R01|R1|P|2.4" + "\r" + HBA1C
    report, issues = extract(msg)
    assert report is None and [i.code for i in issues] == ["ingest.no-pid"]
