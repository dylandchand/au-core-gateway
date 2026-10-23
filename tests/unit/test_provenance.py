"""'Where this data comes from' (gateway/provenance.py) must say what the generators really do."""

import ast
import json
from pathlib import Path

from gateway import provenance

SCRIPTS = Path("scripts")


def _constant(script: str, name: str):
    tree = ast.parse((SCRIPTS / script).read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {script}")


def test_the_table_matches_the_generators():
    assert provenance.FIXTURE_SEED == _constant("make_fixtures.py", "SEED")
    assert provenance.QLD_SUBURBS_EXAMPLES == len(_constant("make_fixtures.py", "SUBURBS"))
    assert provenance.QLD_SUBURBS_FEED == len(_constant("synthea_to_hl7.py", "QLD_SUBURBS"))
    # The lab and hospital the table calls placeholders are the ones the generators write.
    for script in ("make_fixtures.py", "synthea_to_hl7.py"):
        assert "DEMOLAB|QLDDEMO" in (SCRIPTS / script).read_text()


def test_examples_are_described_from_the_fixtures_themselves():
    src = provenance.describe([Path("fixtures/golden"), Path("fixtures/faults")])
    assert [s["kind"] for s in src] == ["examples", "examples"]
    files = len(list(Path("fixtures/golden").glob("*.hl7"))) + len(list(Path("fixtures/faults").glob("*.hl7")))
    assert f"{files} example files" in provenance.summary(src)


def test_a_feed_says_what_made_it_and_a_faulted_copy_adds_its_mistakes(tmp_path):
    feed = tmp_path / "feed"
    (feed / "adt").mkdir(parents=True)
    (feed / provenance.MANIFEST).write_text(json.dumps(provenance.feed_manifest("v3.4.0", 7, "Massachusetts", 12)))
    (feed / "faults.json").write_text(json.dumps({"seed": 9, "faulted_messages": 3, "faults": []}))
    [s] = provenance.describe([feed])
    assert (s["kind"], s["release"], s["patients"], s["planted"]) == ("synthea", "v3.4.0", 12, 3)
    text = provenance.summary([s])
    assert "12 patients" in text and "Synthea</a> v3.4.0 (seed 7)" in text and "3 mistakes planted" in text
    s["slice"] = {"messages": 5, "planted": 1}
    assert provenance.summary([s]).startswith("This page uses 5 messages from a synthetic population of 12 patients")


def test_an_input_with_no_record_of_its_origin_is_never_called_synthetic(tmp_path):
    (tmp_path / "x.hl7").write_text("MSH|^~\\&|A")
    src = provenance.describe([tmp_path])
    assert src[0]["kind"] == "unknown"
    page = provenance.html(src)
    assert "did not record" in page and "<table" not in page      # no claims about data it can't vouch for


def test_rows_fit_the_data_present_and_the_page_escapes_its_text():
    ex = provenance.describe([Path("fixtures/golden")])
    details = [d for d, _, _ in provenance.rows(ex)]
    assert "Mistakes" not in details and "Names" in details
    assert all(real in (provenance.INVENTED, provenance.PLACE, provenance.NUMBER) for _, _, real in provenance.rows(ex))
    assert "<script" not in provenance.html([{"kind": "unknown", "path": "<script>", "files": 1}])
