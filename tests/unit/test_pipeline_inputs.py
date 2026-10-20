"""How a run treats input files it cannot read (no validator needed: nothing is translated)."""

from gateway import pipeline


def test_an_unreadable_file_is_one_error_and_the_run_carries_on(tmp_path):
    feed = tmp_path / "feed"
    feed.mkdir()
    (feed / "latin1.hl7").write_bytes("MSH|^~\\&|L|F|G|A|2026||ADT^A04|C1|P|2.4\rPID|1||1^^^X^MR||ROSÉ\r".encode("latin-1"))
    (feed / "notes.hl7").write_text("not hl7 at all\r")
    report = pipeline.run([feed], "demo-lab", tmp_path / "out")
    by_file = {r["source_file"]: r for r in report["records"]}
    assert [i["message_id"] for i in by_file["latin1.hl7"]["issues"]] == ["ingest.bad-encoding"]
    assert by_file["latin1.hl7"]["issues"][0]["source"] == "latin1.hl7:2 "
    assert [i["message_id"] for i in by_file["notes.hl7"]["issues"]] == ["ingest.parse-error"]
