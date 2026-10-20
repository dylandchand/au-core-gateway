"""Lab results -> AU Core pathology Observations (no validator)."""

import json

import pytest

from gateway.ingest.hl7v2 import parse_text
from gateway.ingest.lab_adapter import extract_lab_report
from gateway.ingest.patient_adapter import load_rules
from gateway.mapping.fhir_json import dumps, fhir_decimal
from gateway.mapping.observation_builder import AU_CORE_PATH_RESULT, build_observation
from gateway.mapping.trace import locate
from gateway.pipeline import ObservationSelection

RULES = load_rules("demo-lab")
MSH = r"MSH|^~\&|DEMOLAB|QLD|GW|AU|20261012083000+1000||ORU^R01^ORU_R01|R1|P|2.4"
PID = "PID|1||8003608333647261^^^AUSHIC^NI||CITIZEN^JANE^^^^^L||19800215|F"
NM = "OBX|1|NM|4548-4^Hemoglobin A1c^LN||6.10|%^^UCUM|||||F|||20261012083000+1000"
ST = "OBX|2|ST|5767-9^Appearance of Urine^LN||Cloudy urine (finding)||||||F|||20261012083000+1000"


def build(*obx: str):
    report, _ = extract_lab_report(parse_text("\r".join([MSH, PID, *obx]), file="lab.hl7")[0], RULES)
    return [build_observation(report, r, "jane", RULES) for r in report.results]


def test_numeric_result_shape():
    (obs, trace, issues), = build(NM)
    assert issues == []
    assert obs["meta"]["profile"] == [AU_CORE_PATH_RESULT]
    assert obs["status"] == "final"
    assert obs["category"] == [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category",
                                            "code": "laboratory"}]}]
    assert obs["code"]["coding"] == [{"system": "http://loinc.org", "code": "4548-4", "display": "Hemoglobin A1c"}]
    assert obs["subject"] == {"reference": "Patient/jane"}
    assert obs["effectiveDateTime"] == "2026-10-12T08:30:00+10:00"
    assert obs["valueQuantity"] == {"value": "6.10", "unit": "%", "system": "http://unitsofmeasure.org", "code": "%"}
    assert obs["text"]["status"] == "generated"


def test_precision_survives_into_the_json():
    (obs, _, _), = build(NM)
    written = dumps(obs)
    assert '"value": 6.10' in written                      # not 6.1, not "6.10"
    assert json.loads(written)["valueQuantity"]["value"] == 6.1


@pytest.mark.parametrize("raw,expected", [("2.80", "2.80"), ("+1", "1"), (".5", "0.5"), ("5.", "5"),
                                          ("007.10", "7.10"), ("-0.30", "-0.30"), ("0", "0")])
def test_decimal_text_is_valid_json_with_the_same_digits(raw, expected):
    assert fhir_decimal(raw) == expected
    assert json.loads(dumps({"v": fhir_decimal(raw)}))["v"] == float(expected)


def test_text_result_becomes_value_string():
    _, (obs, _, issues) = build(NM, ST)
    assert issues == []
    assert obs["status"] == "final"
    assert obs["valueString"] == "Cloudy urine (finding)" and "valueQuantity" not in obs


def test_unknown_status_and_coding_system_are_flagged_not_guessed():
    (obs, _, issues), = build(NM.replace("|||||F|||", "|||||Z|||"))
    assert "status" not in obs
    assert [(i.code, i.source.locator) for i in issues] == [("mapping.unmapped-code", "OBX(1)-11")]
    (obs, _, issues), = build(NM.replace("^LN||", "^XX||"))
    assert "coding" not in obs["code"] and obs["code"]["text"] == "Hemoglobin A1c"
    assert [(i.code, i.source.locator) for i in issues] == [("mapping.unmapped-code", "OBX(1)-3.3")]


def test_unit_without_known_system_is_kept_as_text():
    (obs, _, issues), = build(NM.replace("%^^UCUM", "%^^LOCAL"))
    assert obs["valueQuantity"] == {"value": "6.10", "unit": "%"}
    assert [i.code for i in issues] == ["mapping.unit-not-coded"]


@pytest.mark.parametrize("expression,message,message_id,expected", [
    ("Observation.value.ofType(Quantity)", "", "", "OBX(1)-5"),
    ("Observation.value.ofType(Quantity).code", "", "", "OBX(1)-6"),
    ("Observation.code", "", "", "OBX(1)-3"),
    ("Observation.effective.ofType(dateTime)", "", "", "OBX(1)-14"),
    ("Observation", "Observation.status: minimum required = 1, but only found 0", "", "OBX(1)-11"),
    ("Observation", "Constraint failed", "http://x#au-core-obs-01", "OBX(1)-5"),
    ("Observation.subject", "", "", "PID-3"),
])
def test_validator_expressions_trace_back_to_obx_fields(expression, message, message_id, expected):
    (_, trace, _), = build(NM)
    assert locate(trace, expression, message, message_id).locator == expected


def observation(oid, code="4548-4", unit="%"):
    return {"id": oid, "status": "final", "code": {"coding": [{"system": "http://loinc.org", "code": code}]},
            "valueQuantity": {"value": "1", "system": "http://unitsofmeasure.org", "code": unit}}


def test_selection_validates_everything_in_small_runs():
    sel = ObservationSelection(validate_all=True)
    assert all(sel.choose(observation(f"o{n}")) for n in range(20))
    assert sel.summary()["validated"] == 20


def test_selection_covers_every_shape_and_is_deterministic():
    picks = []
    for _ in range(2):
        sel = ObservationSelection(validate_all=False)
        chosen = [sel.choose(observation(f"o{n}", code=("A", "B")[n % 2], unit=("%", "mg")[n % 3 == 0]))
                  for n in range(3000)]
        picks.append(chosen)
        s = sel.summary()
        assert s["shapes_validated"] == s["shapes"] == 4
        assert 4 <= s["validated"] < 60                      # one per shape + about 0.5%
    assert picks[0] == picks[1]


def test_built_in_examples_are_always_validated():
    # Their expectations name validator findings, so sampling must never skip them.
    sel = ObservationSelection(validate_all=False, per_thousand=0)
    assert sel.choose(observation("o0"))
    assert not sel.choose(observation("o1"))
    assert sel.choose(observation("o2"), always=True)


def test_result_time_precision_is_part_of_the_shape():
    # AU Core requires a time to at least the day (au-core-obs-02): a result with no time or
    # a year-only time must reach the validator even when it is otherwise a common shape.
    sel = ObservationSelection(validate_all=False, per_thousand=0)
    base = observation("o0") | {"effectiveDateTime": "2024-01-15T08:30:00+10:00"}
    assert sel.choose(base)
    assert not sel.choose(base | {"id": "o1"})
    assert sel.choose({k: v for k, v in base.items() if k != "effectiveDateTime"} | {"id": "o2"})
    assert sel.choose(base | {"id": "o3", "effectiveDateTime": "2024"})


def test_observation_ids_stay_unique_when_set_ids_repeat():
    # Real feeds restart OBX-1 under each OBR; ids come from the OBX occurrence instead.
    ids = [obs["id"] for obs, _, _ in build(NM, NM)]
    assert len(set(ids)) == 2 and ids[0].endswith("-1") and ids[1].endswith("-2")
