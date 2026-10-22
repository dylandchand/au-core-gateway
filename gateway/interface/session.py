"""One interface connection's worth of messages, processed as they arrive.

A real interface receives one message at a time and must answer each with an ACK. A Session
keeps what a receiving system would remember between messages (the patient registrations
already accepted, so later lab reports can be filed against them) and runs every message
through the unchanged pipeline (via gateway.inspector.bridge, the same path the Message
Inspector uses), with the official validator off: an interface acknowledges on the gateway's
own checks; official validation is a separate step (see gateway/interface/ack.py).

Used by the local MLLP listener (gateway/interface/mllp.py) and, in the browser, by the
Interface Monitor page. Nothing here opens a connection.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from gateway.ingest.hl7v2 import HL7ParseError, parse_text, text_lines
from gateway.inspector import bridge
from gateway.interface import ack as acks

REGISTRATIONS = "registered.hl7"


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


class Session:
    def __init__(self, work: Path | None = None, clock: Callable[[], datetime] = _now):
        self.work = Path(work) if work else Path(tempfile.mkdtemp(prefix="gateway-interface-"))
        self.clock = clock
        self.registered: list[str] = []     # texts of accepted registrations, in arrival order
        self.count = 0

    def receive_bytes(self, raw: bytes) -> dict:
        """As receive(), for bytes off the wire. Anything that is not UTF-8 is rejected, never
        decoded by guesswork (the same rule as ingest.bad-encoding for files)."""
        try:
            return self.receive(raw.decode("utf-8-sig"))
        except UnicodeDecodeError as exc:
            self.count += 1
            when = self.clock()
            a = acks.build(acks.REJECT, f"Not UTF-8 text (byte 0x{raw[exc.start]:02X}); not read",
                           f"GW{self.count:08d}", when, None)
            return self._receipt(self.count, when, None, "unreadable", a, [], [], None)

    def receive(self, text: str) -> dict:
        """Process one message (one MLLP frame's content) and return what happened, ACK included."""
        self.count += 1
        n = self.count
        when = self.clock()
        ack_id = f"GW{n:08d}"
        name = f"msg-{n:06d}.hl7"
        lines = text_lines(text)

        try:
            messages = parse_text(text, file=name)
        except HL7ParseError as exc:
            a = acks.build(acks.REJECT, f"Could not read the message: {exc}", ack_id, when, None)
            return self._receipt(n, when, None, "unreadable", a, [], lines, None)
        if len(messages) != 1:
            why = "No message found" if not messages else f"{len(messages)} messages in one frame; send one at a time"
            a = acks.build(acks.REJECT, why, ack_id, when, messages[0] if messages else None)
            return self._receipt(n, when, messages[0] if messages else None, "unreadable", a, [], lines, None)
        msg = messages[0]

        context = [(REGISTRATIONS, "\n".join(self.registered))] if self.registered else []
        result = bridge.inspect(text, name, context, self.work)
        record = result["records"][0] if result["records"] else None
        issues = record["issues"] if record else []
        errors = [i for i in issues if i["category"] == "error"]
        warnings = [i for i in issues if i["category"] == "warning"]

        if record is None or record["kind"] in ("unsupported", "unparseable"):
            reason = (errors or warnings or [{"label": "Message could not be processed"}])[0]
            code, words = acks.REJECT, self._words(reason)
        elif errors:
            code = acks.ERROR
            words = self._words(errors[0]) + (f" (+{len(errors) - 1} more)" if len(errors) > 1 else "")
        else:
            code = acks.ACCEPT
            words = "Accepted" + (f" with {len(warnings)} warning{'s' if len(warnings) > 1 else ''}"
                                  if warnings else "")
        a = acks.build(code, words, ack_id, when, msg)
        if record and record["kind"] == "patient" and record["resources"]:
            self.registered.append(text.strip("\r\n"))
        return self._receipt(n, when, msg, record["kind"] if record else "unreadable", a, issues, lines, record)

    @staticmethod
    def _words(issue: dict) -> str:
        where = issue.get("source_locator") or ""
        return f"{issue.get('label') or issue.get('message_id')}" + (f" at {where}" if where else "")

    @staticmethod
    def _receipt(n, when, msg, kind, a, issues, lines, record) -> dict:
        return {
            "n": n, "received": when.isoformat(timespec="seconds"),
            "control_id": msg.control_id if msg else "", "type": msg.message_type if msg else "",
            "kind": kind, "code": a.code, "code_name": acks.CODE_NAMES[a.code], "text": a.text,
            "ack": a.message, "issues": issues, "lines": lines,
            "record_id": record["record_id"] if record else None,
            "linked_patient": record.get("linked_patient") if record else None,
            "resources": len(record["resources"]) if record else 0,
        }
