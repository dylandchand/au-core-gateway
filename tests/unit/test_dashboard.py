"""Dashboard generator: data assembly and safe embedding (no browser needed)."""

import json
import re
from pathlib import Path

from gateway.dashboard import build as dash


def record(rid, kind="patient", status="pass", issues=(), lines=(1, 2), source="feed.hl7", **extra):
    issues = list(issues)
    counts = {c: sum(i["category"] == c for i in issues) for c in ("error", "could-not-check", "warning", "info")}
    return {"kind": kind, "record_id": rid, "source_file": source, "source_path": source,
            "lines": list(lines), "resource": f"{rid}.json" if kind == "patient" else None,
            "issues": issues, "status": status, "counts": counts, **extra}


def issue(mid, category="error", stage="validator", at="PID-3[1]", line=2):
    return {"stage": stage, "category": category, "message_id": mid, "expression": "Patient.identifier[0]",
            "message": f"msg for {mid}", "source": f"feed.hl7:{line} {at}", "source_locator": at}


def report(records):
    return {"generated": "2026-10-19T00:00:00+00:00", "validator": "FHIR Validation tool Version 7.0.2",
            "versions": {"hl7.fhir.au.core": "2.0.0"}, "terminology_server": "n/a", "source_rules": "demo-lab",
            "timing_seconds": {}, "package_substitutions": [], "summary": {"records": len(records)},
            "records": records}


def write_sources(tmp_path: Path) -> Path:
    (tmp_path / "feed.hl7").write_text("MSH|^~\\&|A|B\rPID|1||800360</script>^^^AUSHIC^NI\rMSH|^~\\&|A|B\rPID|1||2\r")
    res = tmp_path / "out" / "resources"
    res.mkdir(parents=True)
    for rid in ("p1", "p2"):
        (res / f"{rid}.json").write_text(json.dumps({"resourceType": "Patient", "id": rid}))
    return res


def test_rows_cover_every_record_and_issue_index(tmp_path):
    res = write_sources(tmp_path)
    recs = [record("p1", status="fail", issues=[issue("x#inv-ihi-value-2")]),
            record("p2", lines=(3, 4)),
            record("l1", kind="lab-report", linked_patient="p1", result_count=3, lines=(3, 4))]
    data = dash.build_data(report(recs), tmp_path, res)
    assert [r[0] for r in data["rows"]] == ["p1", "p2", "l1"]
    p1, _, l1 = data["rows"]
    assert p1[2] == 0 and p1[3] == 1                      # failed, one error
    assert data["issues"][p1[10][0]]["label"] == "IHI check digit fails"
    assert l1[8] == 0 and l1[9] == 3                      # linked to row 0, 3 results


def test_unlinked_lab_report_is_marked(tmp_path):
    res = write_sources(tmp_path)
    data = dash.build_data(report([record("l1", kind="lab-report", linked_patient=None)]), tmp_path, res)
    assert data["rows"][0][8] == -2


def test_detail_carries_hl7_lines_fhir_and_traced_issue_line(tmp_path):
    res = write_sources(tmp_path)
    data = dash.build_data(report([record("p1", status="fail", issues=[issue("Validation_VAL_Profile_Minimum")])]), tmp_path, res)
    d = data["detail"]["p1"]
    assert d["hl7"] == [[1, "MSH|^~\\&|A|B"], [2, "PID|1||800360</script>^^^AUSHIC^NI"]]
    assert d["fhir"] == {"resourceType": "Patient", "id": "p1"}
    assert d["issues"][0]["line"] == 2 and d["issues"][0]["at"] == "PID-3[1]"


def test_large_runs_embed_problems_and_a_sample_only(tmp_path, monkeypatch):
    monkeypatch.setattr(dash, "EMBED_ALL_BELOW", 5)
    monkeypatch.setattr(dash, "SAMPLE_PATIENTS", 2)
    recs = [record(f"p{n}") for n in range(10)] + [record("bad", status="fail", issues=[issue("e")]),
                                                   record("mismatch", kind="lab-report", linked_patient="p9",
                                                          issues=[issue("link.demographic-mismatch", "warning", "link")])]
    chosen = dash.select_detail(recs)
    assert chosen == {"p0", "p1", "bad", "mismatch"}


def test_small_runs_embed_everything():
    recs = [record(f"p{n}") for n in range(3)]
    assert dash.select_detail(recs) == {"p0", "p1", "p2"}


def test_embedded_json_cannot_close_the_script_element(tmp_path):
    res = write_sources(tmp_path)
    data = dash.build_data(report([record("p1")]), tmp_path, res)
    page = dash.render(data, fragment=True)
    blob = re.search(r'<script type="application/json" id="gw-data">(.*?)</script>', page, re.S).group(1)
    assert "</script>" not in blob and "<\\/script>" in blob
    assert json.loads(blob)["detail"]["p1"]["hl7"][1][1].endswith("</script>^^^AUSHIC^NI")


def test_full_page_has_head_and_fragment_does_not(tmp_path):
    res = write_sources(tmp_path)
    data = dash.build_data(report([record("p1")]), tmp_path, res)
    full, frag = dash.render(data), dash.render(data, fragment=True)
    assert full.startswith("<!doctype html>") and "<title>AU Core Gateway</title>" in full.split("</head>")[0]
    assert not frag.lstrip().lower().startswith("<!doctype") and "<html" not in frag


def test_every_label_has_text():
    for key, (label, hint) in dash.LABELS.items():
        assert label and hint, key


def test_lab_report_detail_embeds_its_observations_flagged_first(tmp_path, monkeypatch):
    monkeypatch.setattr(dash, "OBSERVATIONS_PER_REPORT", 2)
    res = write_sources(tmp_path)
    for n in range(3):
        (res / f"l1-{n}.json").write_text(json.dumps({"resourceType": "Observation", "id": f"l1-{n}"}))
    bad = issue("Validation_VAL_Profile_Minimum", at="OBX(3)-11", line=4) | {"resource": "l1-2.json"}
    recs = [record("p1"),
            record("l1", kind="lab-report", status="fail", linked_patient="p1", result_count=4, lines=(3, 4),
                   observations=3, resources=["l1-0.json", "l1-1.json", "l1-2.json"], issues=[bad])]
    data = dash.build_data(report(recs), tmp_path, res)
    assert data["rows"][1][11:] == [3, 3]                 # built, validated
    d = data["detail"]["l1"]
    assert [o["file"] for o in d["observations"]] == ["l1-2.json", "l1-0.json"]
    assert d["observations"][0]["json"]["id"] == "l1-2"
    assert d["issues"][0]["resource"] == "l1-2.json"


def test_embedded_decimals_keep_their_digits(tmp_path):
    path = tmp_path / "o.json"
    path.write_text('{"resourceType": "Observation", "valueQuantity": {"value": 75.0, "unit": "mg/dL"}, "n": 3}')
    assert dash.load_resource(path)["valueQuantity"]["value"] == {"$decimal": "75.0"}
    assert dash.load_resource(path)["n"] == 3
