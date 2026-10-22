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
