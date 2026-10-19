"""Injected faults are all detected, at the right field, by a real run (real validator)."""

from __future__ import annotations

import pytest

from gateway import pipeline
from tests.fault_feed import load_script, small_feed

pytestmark = pytest.mark.validator
inject = load_script("inject_faults")


@pytest.fixture(scope="module")
def scored(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("faults")
    feed = small_feed(tmp / "feed")
    inject.inject(feed, tmp / "faulted", per_type=1)
    clean = pipeline.run([feed], "demo-lab", tmp / "out-clean")
    faulted = pipeline.run([tmp / "faulted"], "demo-lab", tmp / "out", fault_ledger=tmp / "faulted" / "faults.json")
    return clean, faulted


def test_the_clean_feed_passes(scored):
    clean, _ = scored
    assert clean["summary"]["failed"] == 0


def test_every_injected_fault_is_detected_at_its_field(scored):
    f = scored[1]["faults"]
    missed = {t["name"]: t["missed"] for t in f["types"] if t["missed"]}
    assert f["detected"] == f["injected"] == len(inject.CATALOGUE), missed


def test_faults_cause_nothing_beyond_their_own_field(scored):
    assert scored[1]["faults"]["collateral_findings"] == 0, [f for f in scored[1]["faults"]["faults"] if f["collateral"]]


def test_no_false_alarms_on_untouched_records(scored):
    c = scored[1]["faults"]["clean_records"]
    assert c["total"] > 0 and c["false_alarms"] == 0, c["false_alarm_examples"]
    assert c["passed"] == c["total"]
