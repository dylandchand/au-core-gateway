"""Scale run: a seeded Synthea population through the whole gateway, checked against the
source data (round trip). This is the Phase 2 gate.

Run with:  pytest -m scale          (needs Java and `make setup`; several minutes)
Size:      GATEWAY_SCALE_PATIENTS (default 1000 living patients; Synthea adds the deceased)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from gateway import pipeline

ROOT = Path(__file__).resolve().parents[2]
N = int(os.environ.get("GATEWAY_SCALE_PATIENTS", "1000"))
DATA = ROOT / "data" / f"synthea-{N}"

pytestmark = pytest.mark.scale


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    if not (DATA / "truth.json").exists():
        subprocess.run([sys.executable, str(ROOT / "scripts" / "generate_population.py"),
                        "--patients", str(N)], check=True)
    out = tmp_path_factory.mktemp("scale")
    report = pipeline.run([DATA / "adt", DATA / "oru"], "demo-lab", out)
    pipeline.print_summary(report, max_failures=10)
    truth = json.loads((DATA / "truth.json").read_text())
    return report, truth, out


def ihi_of(patient: dict) -> str:
    return next(i["value"] for i in patient["identifier"] if i["type"]["coding"][0]["code"] == "NI")


def test_every_patient_conforms_to_au_core(run):
    report, truth, _ = run
    patients = [r for r in report["records"] if r["kind"] == "patient"]
    assert len(patients) == len(truth) >= N
    failing = [(r["record_id"], [i["message_id"] for i in r["issues"] if i["category"] == "error"])
               for r in patients if r["status"] != "pass"]
    assert failing == []


def test_every_lab_report_links_to_its_patient(run):
    report, truth, _ = run
    s = report["summary"]["lab_reports"]
    assert s["orphans"] == 0
    assert s["linked"] == s["total"] == sum(t["lab_reports"] for t in truth.values())
    assert s["results"] == sum(t["lab_results"] for t in truth.values())


def test_no_errors_anywhere(run):
    report, _, _ = run
    assert report["summary"]["error"] == 0


def test_round_trip_matches_synthea_source(run):
    """What comes out of the gateway equals what Synthea generated, patient by patient."""
    report, truth, out = run
    record_to_ihi, mismatches = {}, Counter()
    for r in report["records"]:
        if r["kind"] != "patient":
            continue
        patient = json.loads((out / "resources" / r["resource"]).read_text())
        ihi = ihi_of(patient)
        record_to_ihi[r["record_id"]] = ihi
        expected = truth[ihi]
        name = patient["name"][0]
        for field, got in (("family", name.get("family")), ("given", name.get("given", [])),
                           ("birthDate", patient.get("birthDate")), ("gender", patient.get("gender"))):
            if got != expected[field]:
                mismatches[field] += 1
    reports, results = Counter(), Counter()
    for r in report["records"]:
        if r["kind"] == "lab-report":
            ihi = record_to_ihi[r["linked_patient"]]
            reports[ihi] += 1
            results[ihi] += r["result_count"]
    for ihi, expected in truth.items():
        mismatches["lab_reports"] += reports[ihi] != expected["lab_reports"]
        mismatches["lab_results"] += results[ihi] != expected["lab_results"]
    assert +mismatches == Counter()


def test_every_result_becomes_an_observation_and_every_shape_is_validated(run):
    report, truth, _ = run
    obs = report["summary"]["observations"]
    assert obs["built"] == sum(t["lab_results"] for t in truth.values())
    assert obs["not_converted"] == 0
    assert obs["shapes_validated"] == obs["shapes"]
    assert obs["failed_validation"] == 0
