"""Interface Monitor (gateway/monitor/): a page that replays a synthetic feed through the same
Session as the local listener. Everything it stages comes from the project's own fixtures."""

import json
import re
import zipfile
from pathlib import Path

import yaml

from gateway.interface import ack as acks
from gateway.interface.session import Session
from gateway.monitor import build

EXPECT = yaml.safe_load(Path("fixtures/expectations.yaml").read_text())


def _data(html):
    return json.loads(re.search(r'<script id="mon-data" type="application/json">(.*?)</script>', html, re.S).group(1))


def test_page_is_complete_and_carries_the_gateway(tmp_path):
    page = build.build(tmp_path)
    html = page.read_text()
    assert not re.findall(r"__[A-Z_]+__", html), "a placeholder was left unfilled"
    names = zipfile.ZipFile(tmp_path / "gateway.zip").namelist()
    assert any(n.endswith("gateway/interface/session.py") for n in names)
    d = _data(html)
    assert d["feed"] and not d["synthea"]               # without a feed: the built-in examples only
    assert d["pyodide"] in html


def test_without_a_feed_every_example_is_accepted_in_order(tmp_path):
    s = Session(tmp_path)
    assert {s.receive(m["text"])["code"] for m in build.feed(None)} == {acks.ACCEPT}


def test_staged_message_faults_are_the_projects_own_fixtures():
    for sc in build.scenarios():
        if sc["kind"] != "send" or sc["id"] == "a03":
            continue
        name = next(f for f in Path("fixtures/faults").glob("*.hl7")
                    if build.split_messages(f.read_text())[0] == sc["text"])
        assert EXPECT["faults"][name.name]["errors"], f"{name.name} has no expected error"
    ihi = next(sc for sc in build.scenarios() if sc["id"] == "ihi")
    assert ihi["text"] == build.split_messages(Path("fixtures/faults/ihi-bad-check-digit.hl7").read_text())[0]
    # "accepted, but the validator rejects it": the validator error is in the expectations
    assert EXPECT["faults"]["ihi-bad-check-digit.hl7"]["errors"][0]["id"].endswith("#inv-ihi-value-2")


def test_staged_faults_get_the_acks_the_page_describes(tmp_path):
    s = Session(tmp_path)
    for m in build.feed(None):
        s.receive(m["text"])
    got = {sc["id"]: s.receive(sc["text"])["code"] for sc in build.scenarios() if sc["kind"] == "send"}
    assert got == {"orphan": "AE", "comma": "AE", "loinc": "AE", "status": "AE", "a03": "AR", "ihi": "AA"}


def test_every_scenario_has_triage_steps():
    for sc in build.scenarios():
        assert sc["kind"] in ("send", "network") and len(sc["steps"]) >= 3
        assert ("text" in sc) == (sc["kind"] == "send")


def _feed_dir(tmp_path):
    """A tiny Synthea-shaped feed: the example registrations and lab reports, one lab report with a
    decimal comma, and a ledger that plants it plus a fault only the official validator catches."""
    (tmp_path / "adt").mkdir(parents=True)
    (tmp_path / "oru").mkdir()
    (tmp_path / "adt" / "a.hl7").write_text(Path("fixtures/golden/clean-patients.hl7").read_text())
    (tmp_path / "oru" / "o.hl7").write_text(Path("fixtures/golden/lab-results.hl7").read_text() + "\n"
                                            + Path("fixtures/faults/lab-non-numeric-value.hl7").read_text())
    fault_id = "o-" + build.split_messages(Path("fixtures/faults/lab-non-numeric-value.hl7").read_text())[0] \
        .split("\r")[0].split("|")[9]
    ledger = {"types": [
        {"name": "non-numeric-value", "what": "decimal comma", "expect": [{"category": "error", "id": "ingest.bad-numeric"}]},
        {"name": "ihi-check-digit", "what": "IHI digit", "expect": [{"category": "error", "id": "http://x#inv-ihi-value-2"}]}],
        "faults": [{"fault": "non-numeric-value", "record_id": fault_id}, {"fault": "ihi-check-digit", "record_id": "a-GOLD0003"}]}
    (tmp_path / "faults.json").write_text(json.dumps(ledger))
    return tmp_path


def test_feed_always_carries_its_planted_faults_and_nothing_else_fails(tmp_path):
    feed = build.feed(_feed_dir(tmp_path / "feed"))
    planted = [m for m in feed if m.get("planted")]
    assert [m["planted"] for m in planted].count("decimal comma") == 1       # gateway-caught fault: planted once
    assert any("ADT^A03" in m["planted"] for m in planted)                   # and the unsupported message type
    synthea = [m for m in feed if m["source"] == "synthea"]
    assert not any("|GOLD0003|" in m["text"] for m in synthea)               # validator-only fault: left out
    s = Session(tmp_path / "work")
    for m in feed:
        r = s.receive(m["text"])
        clean = r["code"] == acks.ACCEPT and not any(i["category"] == "warning" for i in r["issues"])
        assert clean != bool(m.get("planted")), (r["control_id"], r["text"], m.get("planted"))


def test_scenarios_have_labels_for_the_bar_and_the_guide():
    for sc in build.scenarios():
        assert sc["chip"] and sc["short"] and sc["action"] and len(sc["chip"]) <= 20
