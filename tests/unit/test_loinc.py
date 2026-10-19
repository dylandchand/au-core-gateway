"""LOINC Mod 10 check digit (LOINC Users' Guide, Appendix C) and the ingest check that uses it."""

import pytest

from gateway.identifiers.loinc import loinc_problem, mod10
from gateway.ingest.hl7v2 import parse_text
from gateway.ingest.lab_adapter import extract_lab_report
from gateway.ingest.patient_adapter import load_rules

RULES = load_rules("demo-lab")


def test_users_guide_worked_example():
    assert mod10("12345") == 5


@pytest.mark.parametrize("code", ["4548-4", "2339-0", "5767-9", "33914-3", "718-7", "2093-3"])
def test_codes_used_by_the_feed_pass(code):
    assert loinc_problem(code) is None


# The 14 deprecated codes LOINC lists as having stored check digits that the algorithm does not
# produce (LOINC forum "LOINC Code and Mod 10 Check Digit Mismatch", confirmed by LOINC staff).
# Matching all 14 computed digits is an independent check that mod10() follows Appendix C.
KNOWN_MISMATCHES = [("11491", 6, 8), ("11501", 6, 4), ("5895", 7, 8), ("5896", 5, 6), ("5897", 3, 4),
                    ("5928", 1, 7), ("6028", 3, 5), ("6415", 5, 4), ("6580", 7, 5), ("6645", 7, 6),
                    ("6646", 5, 4), ("6647", 3, 2), ("6648", 1, 0), ("6649", 9, 8)]


@pytest.mark.parametrize("digits,stored,computed", KNOWN_MISMATCHES)
def test_known_deprecated_codes_with_wrong_check_digits_fail(digits, stored, computed):
    assert mod10(digits) == computed
    assert loinc_problem(f"{digits}-{stored}") is not None


@pytest.mark.parametrize("code", ["4548-5", "4548", "4548-44", "ABC-1", "-4"])
def test_wrong_digit_or_format_is_reported(code):
    assert loinc_problem(code) is not None


def report(obx: str):
    text = "\r".join([r"MSH|^~\&|L|Q|G|A|20261012083000+1000||ORU^R01^ORU_R01|R1|P|2.4",
                      "PID|1||8003608333647261^^^AUSHIC^NI||CITIZEN^JANE||19800215|F", obx])
    return extract_lab_report(parse_text(text, file="lab.hl7")[0], RULES)


def test_ingest_flags_a_bad_loinc_code_at_obx3():
    _, issues = report("OBX|1|NM|4548-5^Hemoglobin A1c^LN||6.1|%^^UCUM|||||F|||20261012083000+1000")
    assert [(i.code, i.source.locator) for i in issues] == [("ingest.bad-loinc-code", "OBX(1)-3")]


def test_codes_in_other_systems_are_not_checked_as_loinc():
    _, issues = report("OBX|1|NM|ABC^Local test^L||6.1|%^^UCUM|||||F|||20261012083000+1000")
    assert issues == []
