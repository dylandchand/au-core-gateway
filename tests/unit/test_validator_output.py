"""Parsing the validator's output. Uses a trimmed copy of real validator output, so these
tests run without Java."""

import json
from pathlib import Path

from gateway.validate.validator import categorise, parse_output

SAMPLE = json.loads((Path(__file__).parent / "data" / "validator-output-sample.json").read_text())


def test_bundle_is_split_per_file():
    results = parse_output(SAMPLE)
    assert set(results) == {"good.json", "broken.json"}


def test_issue_fields_are_extracted():
    broken = parse_output(SAMPLE)["broken.json"]
    luhn = next(i for i in broken if i.message_id.endswith("inv-ihi-value-2"))
    assert luhn.severity == "error" and luhn.category == "error"
    assert luhn.expression == "Patient.identifier[0]"
    assert luhn.line == 1 and luhn.col == 4998


def test_categories():
    assert categorise("error", "anything") == "error"
    assert categorise("fatal", "") == "error"
    assert categorise("warning", "Terminology_TX_ValueSet_NotFound") == "could-not-check"
    assert categorise("information", "UNKNOWN_CODESYSTEM") == "could-not-check"
    assert categorise("warning", "Validation_VAL_Profile_MultipleMatches") == "warning"
    assert categorise("information", "x") == "info"


def test_could_not_check_is_never_counted_as_an_error():
    good = parse_output(SAMPLE)["good.json"]
    assert not any(i.category == "error" for i in good)
    assert any(i.category == "could-not-check" for i in good)
