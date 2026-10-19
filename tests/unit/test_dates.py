import pytest

from gateway.ingest.dates import hl7_date_to_iso, hl7_ts_to_iso


@pytest.mark.parametrize("raw,expected", [
    ("20170528185424+1000", ("2017-05-28T18:54:24+10:00", False)),
    ("201705281854+1000", ("2017-05-28T18:54:00+10:00", False)),     # seconds added, same instant
    ("20170528185424.123-0500", ("2017-05-28T18:54:24.123-05:00", False)),
    ("20170528", ("2017-05-28", False)),                             # date only stays a date
    ("201705", ("2017-05", False)),
    ("20170528185424", ("2017-05-28", True)),                        # no timezone: date + flag
    ("2017052818+1000", (None, False)),                              # hour without minutes
    ("20170231120000+1000", (None, False)),                          # 31 February
    ("20171332083000+1000", (None, False)),                          # month 13
    ("20170528250000+1000", (None, False)),                          # hour 25
    ("20170528120000+2500", (None, False)),                          # impossible offset
    ("28/05/2017", (None, False)),
])
def test_hl7_ts_to_iso(raw, expected):
    assert hl7_ts_to_iso(raw) == expected


def test_timezone_is_never_invented():
    value, tz_missing = hl7_ts_to_iso("20170528185424")
    assert "T" not in value and tz_missing


@pytest.mark.parametrize("raw,expected", [
    ("19800215", "1980-02-15"), ("1980", "1980"), ("19800215123000+1000", "1980-02-15"),
    ("19800230", None), ("", None),
])
def test_hl7_date_to_iso(raw, expected):
    assert hl7_date_to_iso(raw) == expected


def test_reduced_precision_with_a_timezone_keeps_its_precision():
    from gateway.ingest.dates import hl7_ts_to_iso
    assert hl7_ts_to_iso("2024+1000") == ("2024", False)
    assert hl7_ts_to_iso("202401+1000") == ("2024-01", False)
    assert hl7_ts_to_iso("20240115+1000") == ("2024-01-15", False)
    assert hl7_ts_to_iso("2024139+1000") == (None, False)
