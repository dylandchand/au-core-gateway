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
    s.receive(first(REG))
    linked = s.receive(first(LAB))
    assert linked["code"] == acks.ACCEPT and linked["linked_patient"]


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
