import pytest

from gateway.ingest.hl7v2 import HL7ParseError, parse_text

MSG = "\r".join([
    r"MSH|^~\&|LAB|QLD|GW|AU|20261019||ADT^A04^ADT_A01|CTRL1|P|2.4",
    r"PID|1||111^^^AUSHIC^NI~222^^^AUSHIC^MC||SMITH\T\JONES^ANNE^^^^^L||19800215|F",
])


def test_msh_field_numbering_counts_the_separator_as_msh1():
    msg = parse_text(MSG)[0]
    assert msg.msh.field(1) == "|"
    assert msg.msh.field(2) == r"^~\&"
    assert msg.message_type == "ADT^A04"
    assert msg.control_id == "CTRL1"


def test_repeats_and_components():
    pid = parse_text(MSG)[0].first("PID")
    assert len(pid.repeats(3)) == 2
    assert pid.value(3, 1, 1) == "111"
    assert pid.value(3, 2, 5) == "MC"
    assert pid.value(7) == "19800215"
    assert pid.value(99) == ""          # absent field is empty, not an error


def test_escape_sequences_are_decoded():
    pid = parse_text(MSG)[0].first("PID")
    assert pid.value(5, 1, 1) == "SMITH&JONES"


def test_custom_encoding_characters_are_honoured():
    text = "MSH#*~\\&#LAB#QLD#GW#AU#20261019##ADT*A04#C2#P#2.4\rPID#1##555*x*x*AUSHIC*NI"
    pid = parse_text(text)[0].first("PID")
    assert pid.value(3, 1, 1) == "555"
    assert pid.value(3, 1, 5) == "NI"


def test_multiple_messages_and_line_numbers_with_mixed_newlines():
    text = MSG + "\n\n" + MSG.replace("CTRL1", "CTRL2").replace("\r", "\r\n")
    msgs = parse_text(text, file="feed.hl7")
    assert [m.control_id for m in msgs] == ["CTRL1", "CTRL2"]
    assert msgs[1].first("PID").line == 5
    assert msgs[0].file == "feed.hl7"


def test_segment_before_msh_is_an_error():
    with pytest.raises(HL7ParseError) as exc:
        parse_text("PID|1||123")
    assert exc.value.line == 1


def test_garbage_line_is_an_error():
    with pytest.raises(HL7ParseError):
        parse_text(MSG + "\rnot a segment at all")
