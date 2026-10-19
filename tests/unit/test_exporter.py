"""The Synthea -> HL7 v2 exporter (scripts/synthea_to_hl7.py)."""

import importlib.util
from pathlib import Path

from gateway.identifiers.au import ihi_problems, medicare_problems
from gateway.ingest.hl7v2 import Encoding, parse_text

spec = importlib.util.spec_from_file_location(
    "synthea_to_hl7", Path(__file__).resolve().parents[2] / "scripts" / "synthea_to_hl7.py")
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)

ROW = {"Id": "b4900f5e-1c24-1150-fd26-6fbc83da8893", "FIRST": "Carlo647", "MIDDLE": "Kory651",
       "LAST": "Blanda868", "BIRTHDATE": "1983-08-06", "GENDER": "M",
       "ADDRESS": "353 Wintheiser Tunnel Suite 8"}
LAB = {"DATE": "2017-10-21T13:46:11Z", "CODE": "4548-4", "TYPE": "numeric", "VALUE": "6.6",
       "UNITS": "%", "DESCRIPTION": "Hemoglobin A1c/Hemoglobin.total in Blood"}
TEXT_LAB = {"DATE": "2017-10-21T13:46:11Z", "CODE": "5767-9", "TYPE": "text",
            "VALUE": "Cloudy | odd ^ text", "UNITS": "{nominal}", "DESCRIPTION": "Appearance of Urine"}


def test_names_lose_synthea_suffixes():
    assert exporter.clean_name("Carlo647") == "CARLO"
    assert exporter.clean_name("O'Keefe12") == "O'KEEFE"


def test_us_suite_becomes_australian_unit():
    assert exporter.au_street("353 Wintheiser Tunnel Suite 8") == "8/353 WINTHEISER TUNNEL"
    assert exporter.au_street("12 Main St") == "12 MAIN ST"


def test_timestamps_convert_to_brisbane_time():
    assert exporter.hl7_ts("2017-10-21T13:46:11Z") == "20171021234611+1000"


def test_localisation_is_deterministic_and_valid():
    a, b = exporter.localise(ROW, 1), exporter.localise(dict(ROW), 1)
    assert a == b
    assert exporter.localise(ROW, 2)["ihi"] != a["ihi"]        # a different seed differs
    assert ihi_problems(a["ihi"]) == [] and medicare_problems(a["medicare"]) == []
    assert (a["family"], a["given"]) == ("BLANDA", ["CARLO", "KORY"])


def test_messages_parse_back_to_the_same_values():
    p = exporter.localise(ROW, 1)
    adt = parse_text(exporter.adt_a04(p, "A0000001"))[0]
    assert adt.message_type == "ADT^A04"
    assert adt.first("PID").value(3, 1, 1) == p["ihi"]
    assert adt.first("PID").value(7) == "19830806"

    oru = parse_text(exporter.oru_r01(p, "R0000001", "LR1", [LAB, TEXT_LAB]))[0]
    assert oru.message_type == "ORU^R01"
    assert oru.first("OBR").value(25) == "F"                      # result status in OBR-25
    numeric, text = oru.all("OBX")
    assert (numeric.value(2), numeric.value(3), numeric.value(5), numeric.value(6)) == ("NM", "4548-4", "6.6", "%")
    assert numeric.value(14) == "20171021234611+1000"
    # Units exactly as Synthea wrote them, {nominal} included (CLAUDE.md: the exporter never
    # alters clinical content; an earlier version dropped it).
    assert (text.value(2), text.value(5), text.value(6)) == ("ST", "Cloudy | odd ^ text", "{nominal}")


def test_escaping_round_trips_every_delimiter():
    enc = Encoding()
    for text in ["a|b^c&d~e\\f", "\\E\\ already escaped looking", "plain"]:
        assert enc.unescape(enc.escape(text)) == text
