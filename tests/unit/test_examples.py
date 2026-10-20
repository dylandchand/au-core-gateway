"""Built-in example checking (gateway/examples.py): the integration tests' exact rule."""

from pathlib import Path

from gateway import examples

EXPECT = {
    "golden": {"clean.hl7": {"records": 2, "all_pass": True}},
    "faults": {
        "bad-ihi.hl7": {"why": "last digit\n  changed", "errors": [{"id": "inv-ihi", "at": "PID-3[1]"}]},
        "name.hl7": {"why": "name differs", "errors": [], "warnings": [{"id": "link.mismatch", "at": "PID-5[1].1"}]},
    },
}


def rec(rid, path, status="pass", issues=()):
    return {"record_id": rid, "source_path": path, "status": status, "issues": list(issues)}


def err(mid, at, category="error"):
    return {"message_id": mid, "source_locator": at, "category": category}


PATHS = {"fixtures/golden/clean.hl7": ("golden", "clean.hl7"),
         "fixtures/faults/bad-ihi.hl7": ("faults", "bad-ihi.hl7"),
         "fixtures/faults/name.hl7": ("faults", "name.hl7")}


def items(result):
    return {i["file"]: i for i in result["broken"]["items"]}


def test_examples_that_behave_exactly_as_expected():
    r = examples.score([rec("c1", "fixtures/golden/clean.hl7"), rec("c2", "fixtures/golden/clean.hl7"),
                        rec("f1", "fixtures/faults/bad-ihi.hl7", "fail", [err("inv-ihi", "PID-3[1]")]),
                        rec("f2", "fixtures/faults/name.hl7", "pass", [err("link.mismatch", "PID-5[1].1", "warning")]),
                        rec("x", "data/other.hl7", "fail", [err("y", "PID-7")])], EXPECT, PATHS)
    assert r["clean"] == {"files": 1, "records": 2, "passed": 2, "expected_records": 2, "failed": []}
    assert (r["broken"]["files"], r["broken"]["as_expected"]) == (2, 2)
    assert items(r)["bad-ihi.hl7"]["why"] == "last digit changed"


def test_wrong_field_or_extra_error_is_not_as_expected():
    r = examples.score([rec("f1", "fixtures/faults/bad-ihi.hl7", "fail", [err("inv-ihi", "PID-3[2]")]),
                        rec("f2", "fixtures/faults/name.hl7", "fail",
                            [err("link.mismatch", "PID-5[1].1", "warning"), err("surprise", "PID-8")])], EXPECT, PATHS)
    bad, name = items(r)["bad-ihi.hl7"], items(r)["name.hl7"]
    assert not bad["as_expected"] and bad["missing"] == [{"category": "error", "id": "inv-ihi", "at": "PID-3[1]"}]
    assert bad["unexpected"] == [{"category": "error", "id": "inv-ihi", "at": "PID-3[2]"}]
    assert not name["as_expected"] and name["unexpected"][0]["id"] == "surprise"
    assert r["broken"]["as_expected"] == 0


def test_a_missing_expected_warning_is_not_as_expected():
    r = examples.score([rec("f2", "fixtures/faults/name.hl7")], EXPECT, {k: v for k, v in PATHS.items() if "name" in k})
    assert items(r)["name.hl7"]["missing"] == [{"category": "warning", "id": "link.mismatch", "at": "PID-5[1].1"}]


def test_no_examples_in_the_run_gives_no_section():
    assert examples.score([rec("x", "data/other.hl7")], EXPECT, {}) is None


def test_group_of_only_matches_the_fixtures_folders(tmp_path):
    (tmp_path / "golden").mkdir()
    (tmp_path / "elsewhere").mkdir()
    assert examples.group_of(tmp_path / "golden" / "a.hl7", tmp_path) == "golden"
    assert examples.group_of(tmp_path / "elsewhere" / "a.hl7", tmp_path) is None


def test_every_fixture_file_has_an_expectation():
    expect = examples.load()
    for group in examples.GROUPS:
        on_disk = {p.name for p in (examples.FIXTURES / group).glob("*.hl7")}
        assert on_disk == set(expect[group]), group
