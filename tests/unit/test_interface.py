"""Interface: ACKs (HL7 v2.4, codes from HL7 table 0008), the per-message session, and MLLP."""

import asyncio
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gateway.ingest.hl7v2 import parse_text
from gateway.interface import ack as acks
from gateway.interface import mllp
from gateway.interface.session import Session

T0 = datetime(2026, 10, 19, 9, 30, tzinfo=timezone(timedelta(hours=10)))
REG = Path("fixtures/golden/clean-patients.hl7")
LAB = Path("fixtures/golden/lab-results.hl7")


def session(tmp_path):
    return Session(tmp_path, clock=lambda: T0)


def first(path, n=0):
    return mllp.split_messages(path.read_text())[n]


def test_ack_echoes_the_original_and_swaps_the_parties(tmp_path):
    r = session(tmp_path).receive(first(REG))
    msh, msa = parse_text(r["ack"])[0].segments
    original = parse_text(first(REG))[0].msh
    assert (msh.field(3), msh.field(4)) == (original.field(5), original.field(6))
    assert (msh.field(5), msh.field(6)) == (original.field(3), original.field(4))
    assert msh.field(9) == "ACK^A04^ACK" and msh.field(11) == "P" and msh.field(12) == "2.4"
    assert msh.field(7) == "20261019093000+1000" and msh.field(10) == "GW00000001"
    assert msa.raw_fields[1:3] == ["AA", "GOLD0001"]


def test_lab_report_is_accepted_only_after_its_patient_is_registered(tmp_path):
    s = session(tmp_path)
    orphan = s.receive(first(LAB))
    assert orphan["code"] == acks.ERROR and "unregistered patient" in orphan["text"]
    s.receive(first(REG, 1))
    linked = s.receive(first(LAB, 1))                    # a report for the patient just registered
    assert linked["code"] == acks.ACCEPT and linked["linked_patient"]


def test_an_unchanged_resend_is_remembered_once(tmp_path):
    s = session(tmp_path)
    for _ in range(5):
        assert s.receive(first(REG))["code"] == acks.ACCEPT
    assert len(s.registered) == 1 and s.count == 5


def test_content_error_is_AE_with_its_field(tmp_path):
    s = session(tmp_path)
    s.receive(first(REG))
    r = s.receive(first(Path("fixtures/faults/lab-non-numeric-value.hl7")))
    assert r["code"] == acks.ERROR and r["text"].endswith("at OBX(2)-5")


def test_unreadable_or_unsupported_is_AR(tmp_path):
    s = session(tmp_path)
    assert s.receive("this is not HL7")["code"] == acks.REJECT
    assert s.receive("MSH|^~\\&|A|B|GATEWAY|AUCORE|2026||ADT^A03^ADT_A03|U1|P|2.4\rPID|1")["code"] == acks.REJECT
    bad = s.receive_bytes(b"MSH|^~\\&|A|B|C|D|2026||ADT^A04|X|P|2.4\rPID|1||\xff")
    assert bad["code"] == acks.REJECT and "Not UTF-8" in bad["text"]
    two = s.receive(first(REG) + "\r" + first(REG, 1))
    assert two["code"] == acks.REJECT and "2 messages" in two["text"]


def test_msa3_fits_and_is_escaped(tmp_path):
    a = acks.build("AE", "x|y^" * 40, "GW1", T0, parse_text(first(REG))[0])
    msa = a.message.split("\r")[1]
    assert len(a.text) == acks.MSA3_MAX and "x\\F\\y\\S\\" in msa


def test_codes_are_hl7_table_0008():
    assert {acks.ACCEPT, acks.ERROR, acks.REJECT} == {"AA", "AE", "AR"}


def test_mllp_round_trip_over_a_real_socket(tmp_path):
    loop = asyncio.new_event_loop()
    started = threading.Event()
    holder = {}

    async def start():
        holder["server"] = await asyncio.start_server(
            lambda r, w: mllp._handle(r, w, Session(tmp_path, clock=lambda: T0), lambda *_: None), "127.0.0.1", 0)
        holder["port"] = holder["server"].sockets[0].getsockname()[1]
        started.set()

    def run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(start())
        loop.run_forever()

    async def stop():
        holder["server"].close()
        await holder["server"].wait_closed()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert started.wait(10)
    try:
        got = mllp.send([REG, LAB], port=holder["port"])
    finally:
        asyncio.run_coroutine_threadsafe(stop(), loop).result(10)
        loop.call_soon_threadsafe(loop.stop)
        t.join(10)
        loop.close()
    codes = [next(s for s in a.split("\r") if s.startswith("MSA")).split("|")[1] for a in got]
    assert codes == ["AA"] * 7


def test_listener_binds_to_this_computer_by_default():
    assert mllp.DEFAULT_HOST == "127.0.0.1" and mllp.DEFAULT_PORT == 2575


# ---------------------------------------------------------------- hold and reprocess

from gateway.interface import session as sessions  # noqa: E402

NUMERIC = Path("fixtures/faults/lab-non-numeric-value.hl7")     # GOLD0001's report, with a decimal comma


def test_a_report_before_its_registration_is_held_then_filed(tmp_path):
    s = session(tmp_path)
    early = s.receive(first(LAB))
    assert early["code"] == acks.ERROR and early["held"] and early["text"].startswith("Held: ")
    assert early["held_count"] == 1 and not early["released"]
    reg = s.receive(first(REG))                          # its patient arrives
    assert reg["code"] == acks.ACCEPT and reg["held_count"] == 0
    [filed] = reg["released"]
    assert filed["n"] == early["n"] and filed["code"] == acks.ACCEPT and filed["linked_patient"]
    assert filed["held_since"] and filed["released"] and filed["record_id"] == early["record_id"]


def test_a_released_report_is_checked_afresh_and_keeps_its_other_errors(tmp_path):
    s = session(tmp_path)
    assert s.receive(first(NUMERIC))["held"]
    [filed] = s.receive(first(REG))["released"]
    assert filed["code"] == acks.ERROR and filed["text"] == "Numeric result is not a number at OBX(2)-5"


def test_only_a_registration_that_shares_an_identifier_releases(tmp_path):
    s = session(tmp_path)
    s.receive(first(LAB))
    other = s.receive(first(REG, 1))                     # a different patient
    assert other["released"] == [] and other["held_count"] == 1


def test_a_resent_held_report_is_held_once(tmp_path):
    s = session(tmp_path)
    s.receive(first(LAB))
    again = s.receive(first(LAB))                        # the same message again: a duplicate
    assert again["duplicate_of"] == 1 and not again["held"] and again["held_count"] == 1


def test_the_hold_queue_is_capped_and_the_ack_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "MAX_HELD", 1)
    s = session(tmp_path)
    s.receive(first(LAB))
    full = s.receive(first(LAB, 1))
    assert not full["held"] and "not held (queue full at 1)" in full["text"] and full["held_count"] == 1
    assert len(parse_text(full["ack"])[0].segments[1].raw_fields[3]) <= acks.MSA3_MAX


def test_a_state_file_carries_registrations_and_held_reports_over_a_restart(tmp_path):
    state = tmp_path / "state.json"
    s = Session(tmp_path / "w1", clock=lambda: T0, state=state)
    s.receive(first(LAB))
    s.receive(first(REG, 1))
    again = Session(tmp_path / "w2", clock=lambda: T0, state=state)
    assert len(again.held) == 1 and len(again.registered) == 1 and again.count == 2
    [filed] = again.receive(first(REG))["released"]
    assert filed["n"] == 1 and again.receive(first(LAB, 1))["code"] == acks.ACCEPT


# ---------------------------------------------------------------- duplicates

def test_an_identical_resend_is_answered_as_before_and_not_processed_twice(tmp_path):
    s = session(tmp_path)
    first_time = s.receive(first(Path("fixtures/faults/lab-non-numeric-value.hl7")))
    again = s.receive(first(Path("fixtures/faults/lab-non-numeric-value.hl7")))
    assert again["duplicate_of"] == first_time["n"] and again["code"] == first_time["code"] == acks.ERROR
    assert again["text"] == f"Duplicate of message {first_time['n']}; not processed again"
    assert again["issues"] == [] and len(s.held) == 1        # held once, not twice
    msa = parse_text(again["ack"])[0].segments[1].raw_fields
    assert msa[1:3] == ["AE", "FAULT0010"]


def test_a_different_message_reusing_a_control_id_is_processed_with_a_warning(tmp_path):
    s = session(tmp_path)
    s.receive(first(REG))
    reused = first(REG, 1).replace(f"|{parse_text(first(REG, 1))[0].control_id}|", "|GOLD0001|", 1)
    r = s.receive(reused)
    assert r["duplicate_of"] is None and r["code"] == acks.ACCEPT and r["text"] == "Accepted with 1 warning"
    assert [(i["message_id"], i["source_locator"]) for i in r["issues"]] == [("interface.reused-control-id", "MSH-10")]
    assert len(s.registered) == 2


def test_the_same_control_id_from_another_sender_is_a_different_message(tmp_path):
    s = session(tmp_path)
    s.receive(first(REG))
    other = first(REG).replace("MSH|^~\\&|DEMOLAB|QLDDEMO|", "MSH|^~\\&|OTHERLAB|QLDDEMO|", 1)
    r = s.receive(other)
    assert r["duplicate_of"] is None and not any(i["message_id"] == "interface.reused-control-id" for i in r["issues"])


def test_what_was_seen_is_capped_and_survives_a_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "MAX_SEEN", 2)
    state = tmp_path / "state.json"
    s = Session(tmp_path / "w1", clock=lambda: T0, state=state)
    for k in range(3):
        s.receive(first(REG, k))
    assert len(s.seen) == 2                                   # the oldest is forgotten first
    again = Session(tmp_path / "w2", clock=lambda: T0, state=state)
    assert again.receive(first(REG, 2))["duplicate_of"] == 3
    assert again.receive(first(REG, 0))["duplicate_of"] is None
