"""End-to-end: fixtures -> gateway -> official HL7 validator -> report, compared with
fixtures/expectations.yaml. Never mocks the validator: that would only test our assumptions.

Run with:  pytest -m validator     (needs Java and `make setup`; about a minute)
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from gateway import pipeline

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
EXPECT = yaml.safe_load((FIXTURES / "expectations.yaml").read_text())

pytestmark = pytest.mark.validator


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    out = tmp_path_factory.mktemp("run")
    return pipeline.run([FIXTURES / "golden", FIXTURES / "faults"], "demo-lab", out)


def records_from(report: dict, file: str) -> list[dict]:
    return [r for r in report["records"] if r["source_file"] == file]


def errors(record: dict) -> set[tuple[str, str]]:
    return {(i["message_id"], i["source_locator"]) for i in record["issues"] if i["category"] == "error"}


def warnings(record: dict) -> set[tuple[str, str]]:
    return {(i["message_id"], i["source_locator"]) for i in record["issues"] if i["category"] == "warning"}


@pytest.mark.parametrize("file", sorted(EXPECT["golden"]))
def test_golden_patients_conform(report, file):
    spec = EXPECT["golden"][file]
    records = records_from(report, file)
    assert len(records) == spec["records"]
    for record in records:
        assert record["status"] == "pass", f"{record['record_id']}: {errors(record)}"


@pytest.mark.parametrize("file", sorted(EXPECT["faults"]))
def test_fault_is_detected_exactly(report, file):
    spec = EXPECT["faults"][file]
    [record] = records_from(report, file)
    expected = {(e["id"], e["at"]) for e in spec["errors"]}
    assert errors(record) == expected, f"why this fixture exists: {spec['why']}"
    assert record["status"] == ("fail" if expected else "pass")
    expected_warnings = {(w["id"], w["at"]) for w in spec.get("warnings", [])}
    assert expected_warnings <= warnings(record), f"missing warnings; why: {spec['why']}"


def test_every_error_is_traced_to_a_source_field(report):
    untraced = [(r["record_id"], i["message_id"]) for r in report["records"]
                for i in r["issues"] if i["category"] == "error" and not i["source_locator"]]
    assert untraced == []


def test_every_fixture_is_covered_by_expectations(report):
    """A new fixture without an expectation would silently prove nothing."""
    files = {r["source_file"] for r in report["records"]}
    assert files == set(EXPECT["golden"]) | set(EXPECT["faults"])


def test_golden_lab_reports_link_to_golden_patients(report):
    linked = {r["linked_patient"] for r in records_from(report, "lab-results.hl7")}
    assert linked == {"clean-patients-GOLD0001", "clean-patients-GOLD0002"}


def test_report_records_versions_and_substitutions(report):
    assert report["versions"]["hl7.fhir.au.core"] == "2.0.0"
    assert "Validation tool" in report["validator"]
    assert isinstance(report["package_substitutions"], list)


def test_golden_lab_results_become_conforming_observations(report):
    for record in records_from(report, "lab-results.hl7"):
        assert record["observations"] == record["result_count"] == len(record["resources"])
        validator_errors = [i for i in record["issues"] if i["stage"] == "validator" and i["category"] == "error"]
        assert validator_errors == []


def test_broken_results_are_not_converted(report):
    """A result that failed an ingest check is not turned into an Observation."""
    [non_numeric] = records_from(report, "lab-non-numeric-value.hl7")
    assert (non_numeric["result_count"], non_numeric["observations"]) == (2, 1)
    [orphan] = records_from(report, "lab-orphan-result.hl7")
    assert orphan["observations"] == 0          # no patient to point at


def test_small_runs_validate_every_observation(report):
    obs = report["summary"]["observations"]
    assert obs["mode"] == "all" and obs["validated"] == obs["built"]


def test_store_reproduces_the_report_numbers(report, tmp_path):
    from gateway import store
    db = store.connect(tmp_path / "history.db")
    run_id = store.save(db, report)
    s = report["summary"]
    assert store.check(db, run_id) == {k: s[k] for k in ("records", "passed", "failed", "error",
                                                          "could-not-check", "warning", "info")}
