"""Message Inspector (gateway/inspector/): the browser page runs the real pipeline unchanged,
so its output must equal the command line's, minus the official validator."""

import json
import zipfile
from pathlib import Path

import pytest

from gateway import examples, pipeline
from gateway.inspector import bridge, build
from gateway.model.canonical import SourceRef
from gateway.validate import validator

FIXTURES = sorted(Path("fixtures").glob("*/*.hl7"))
REG = Path("fixtures/golden/clean-patients.hl7")


def test_validate_false_never_runs_the_validator(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("validator called")
    monkeypatch.setattr(validator, "validate_folder", boom)
    traces = {}
    report = pipeline.run([REG], "demo-lab", tmp_path, validate=False, traces_out=traces)
    assert report["validator"] == pipeline.NOT_VALIDATED
    assert set(traces) == {r["resource"] for r in report["records"]}
    assert all(isinstance(ref, SourceRef) for t in traces.values() for ref in t.values())


def test_json_paths_use_the_dashboard_spelling():
    res = {"resourceType": "Patient", "name": [{"family": "X", "given": ["A"]}], "v": {"$decimal": "2.80"}}
    assert bridge.json_paths(res) == ["Patient", "Patient.name", "Patient.name[0]", "Patient.name[0].family",
                                      "Patient.name[0].given", "Patient.name[0].given[0]", "Patient.v"]


def test_sources_follow_the_trace_and_never_credit_the_root():
    res = {"resourceType": "Observation", "id": "x", "effectiveDateTime": "2026",
           "valueQuantity": {"value": 1, "unit": "%"}}
    trace = {"Observation": SourceRef("f", 4, "OBX(1)"), "Observation.effective": SourceRef("f", 4, "OBX(1)-14"),
             "Observation.valueQuantity": SourceRef("f", 4, "OBX(1)-5"),
             "Observation.valueQuantity.unit": SourceRef("f", 4, "OBX(1)-6")}
    src = bridge.sources(res, trace)
    assert "Observation.id" not in src and "Observation" not in src           # added by the gateway
    assert src["Observation.effectiveDateTime"]["at"] == "OBX(1)-14"           # choice type
    assert src["Observation.valueQuantity.value"]["at"] == "OBX(1)-5"          # longest traced prefix
    assert src["Observation.valueQuantity.unit"] == {"line": 4, "at": "OBX(1)-6", "traced": "Observation.valueQuantity.unit"}


def _native(tmp_path, files):
    out = tmp_path / "native"
    report = pipeline.run(files, "demo-lab", out, validate=False)
    return report, out / "resources"


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.name)
def test_inspector_equals_the_command_line(tmp_path, fixture):
    context = [] if fixture == REG else [(REG.name, REG.read_text())]
    got = bridge.inspect(fixture.read_text(), fixture.name, context, tmp_path / "insp")
    report, resources = _native(tmp_path, [fixture] + ([REG] if context else []))
    want = [r for r in report["records"] if r["source_file"] == fixture.name]
    assert [r["record_id"] for r in got["records"]] == [r["record_id"] for r in want]
    for g, w in zip(got["records"], want):
        assert g["status"] == w["status"]
        assert [{k: v for k, v in i.items() if k not in ("label", "hint")} for i in g["issues"]] == w["issues"]
        files = [w["resource"]] if w.get("resource") else w.get("resources") or []
        assert [r["file"] for r in g["resources"]] == files
        for r in g["resources"]:   # same JSON, decimals as written
            assert r["json"] == bridge._load((resources / r["file"]).read_text())


def test_lab_example_is_filed_against_the_registrations(tmp_path):
    lab = Path("fixtures/golden/lab-results.hl7")
    got = bridge.inspect(lab.read_text(), lab.name, [(REG.name, REG.read_text())], tmp_path)
    assert got["registrations_on_file"] and all(r["kind"] == "lab-report" for r in got["records"])
    assert all(r["linked_patient"] for r in got["records"])
    alone = bridge.inspect(lab.read_text(), lab.name, [], tmp_path)
    assert all(i["message_id"] == "link.orphan-result" for r in alone["records"] for i in r["issues"])


def test_lines_are_numbered_like_the_parser(tmp_path):
    text = "\x0bMSH|^~\\&|A|B|C|D|20261019||ADT^A04|X1|P|2.4\rPID|1||1^^^AUSHIC^NI\r\x1c\r"
    got = bridge.inspect(text, "m.hl7", [], tmp_path)
    assert got["lines"][:2] == ["MSH|^~\\&|A|B|C|D|20261019||ADT^A04|X1|P|2.4", "PID|1||1^^^AUSHIC^NI"]
    assert got["records"][0]["lines"] == [1, 2]


def test_build_refuses_a_substituted_report(tmp_path):
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"package_substitutions": ["x"], "records": []}))
    with pytest.raises(SystemExit):
        build.build(tmp_path / "site", report)


def test_build_writes_page_and_a_reproducible_bundle(tmp_path):
    page = build.build(tmp_path / "a")
    build.build(tmp_path / "b")
    a, b = (tmp_path / "a" / "gateway.zip").read_bytes(), (tmp_path / "b" / "gateway.zip").read_bytes()
    assert a == b
    html = page.read_text()
    assert "__INSPECTOR_DATA__" not in html and "__PYODIDE_VERSION__" not in html
    names = zipfile.ZipFile(tmp_path / "a" / "gateway.zip").namelist()
    assert "gateway/inspector/bridge.py" in names and "versions.toml" in names
    assert "yaml/__init__.py" in names and "yaml/LICENSE" in names          # MIT: licence travels with it
    assert not any(n.endswith((".so", ".pyd")) for n in names)
    data = json.loads(html.split('type="application/json">', 1)[1].split("</script>", 1)[0])
    expect = examples.load()
    assert {e["file"] for e in data["examples"]} == {f.name for f in FIXTURES}
    assert all(len(e["expect"]) == len(expect["faults"][e["file"]].get("errors", []))
               + len(expect["faults"][e["file"]].get("warnings", []) or [])
               for e in data["examples"] if e["group"] == "faults")
    assert data["recorded"] is None


def test_lab_examples_bring_the_registrations():
    data = build.example_list(examples.load())
    by = {e["file"]: e for e in data}
    assert by["lab-results.hl7"]["context"] == [REG.name]
    assert by["clean-patients.hl7"]["context"] == [] and by["missing-sex.hl7"]["context"] == []
