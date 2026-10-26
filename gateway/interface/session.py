"""One interface connection's worth of messages, processed as they arrive.

A real interface receives one message at a time and must answer each with an ACK. A Session
keeps what a receiving system would remember between messages (the patient registrations
already accepted, so later lab reports can be filed against them) and runs every message
through the unchanged pipeline (via gateway.inspector.bridge, the same path the Message
Inspector uses), with the official validator off: an interface acknowledges on the gateway's
own checks; official validation is a separate step (see gateway/interface/ack.py).

Out of order is normal: a lab report can arrive before its patient's registration. Such a report
is answered AE (the gateway's link.orphan-result error, as always) and also held, as interface
engines do; when a registration is later accepted, every held report is tried again and those
that now match a patient are filed ("released"), with the gateway's checks run on them afresh.
A release sends no second ACK: the sender was already answered. The queue is capped (MAX_HELD);
a report that would overflow it is not held, and its ACK says so.

Senders resend: when an ACK is lost on the way back, the same message arrives again. A message
is known by its sender (MSH-3, MSH-4) and control ID (MSH-10). The same message again, byte for
byte, is a duplicate: it is answered with the original's ACK code and not processed twice. A
different message reusing a control ID is processed, with a warning (interface.reused-control-id
at MSH-10), because the sender has a numbering fault. Up to MAX_SEEN messages are remembered.

Used by the local MLLP listener (gateway/interface/mllp.py) and, in the browser, by the
Interface Monitor page. Nothing here opens a connection. With a state file, what the session
remembers (registrations, held reports, the message count) survives a restart of the listener.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from gateway.ingest.hl7v2 import HL7ParseError, parse_text, text_lines
from gateway.inspector import bridge
from gateway.interface import ack as acks

REGISTRATIONS = "registered.hl7"
ORPHAN = "link.orphan-result"
MAX_HELD = 500
MAX_SEEN = 20_000
REUSED = {"message_id": "interface.reused-control-id", "category": "warning", "stage": "interface",
          "label": "Control ID reused by a different message", "source_locator": "MSH-10"}


def _ids(text: str) -> set[str]:
    """The identifier numbers in a message's PID-3 (IHI, Medicare number, MRN): linking only
    ever matches on these, so a held report is retried only when a registration shares one.
    The pipeline's own matching still decides."""
    msg = parse_text(text)[0]
    pid = msg.first("PID")
    if pid is None:
        return set()
    return {pid.value(3, rep, 1) for rep in range(1, len(pid.repeats(3)) + 1)} - {""}


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


class Session:
    def __init__(self, work: Path | None = None, clock: Callable[[], datetime] = _now,
                 state: Path | None = None):
        self.work = Path(work) if work else Path(tempfile.mkdtemp(prefix="gateway-interface-"))
        self.clock = clock
        self.registered: list[str] = []     # texts of accepted registrations, in arrival order
        self._seen: set[str] = set()
        self.held: list[dict] = []          # lab reports waiting for their patient, oldest first
        self.seen: dict[str, dict] = {}     # sender + control ID -> what the gateway answered
        self.count = 0
        self.state = Path(state) if state else None
        if self.state and self.state.is_file():
            saved = json.loads(self.state.read_text(encoding="utf-8"))
            self.registered, self.held, self.count = saved["registered"], saved["held"], saved["count"]
            self.seen = saved.get("seen", {})
            self._seen = set(self.registered)

    def _save(self) -> None:
        if self.state:
            self.state.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state.with_suffix(".tmp")
            tmp.write_text(json.dumps({"registered": self.registered, "held": self.held, "count": self.count,
                                       "seen": self.seen}), encoding="utf-8")
            tmp.replace(self.state)          # never a half-written state file

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
            self._save()
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
            self._save()
            return self._receipt(n, when, None, "unreadable", a, [], lines, None)
        if len(messages) != 1:
            why = "No message found" if not messages else f"{len(messages)} messages in one frame; send one at a time"
            a = acks.build(acks.REJECT, why, ack_id, when, messages[0] if messages else None)
            self._save()
            return self._receipt(n, when, messages[0] if messages else None, "unreadable", a, [], lines, None)
        msg = messages[0]

        key = f"{msg.msh.field(3)}^{msg.msh.field(4)}|{msg.control_id}" if msg.control_id else ""
        digest = hashlib.sha256(text.strip("\r\n").encode("utf-8")).hexdigest()
        before = self.seen.get(key) if key else None
        if before and before["digest"] == digest:
            # A resend: answer as before, process nothing twice.
            words = f"Duplicate of message {before['n']}; not processed again"
            a = acks.build(before["code"], words, ack_id, when, msg)
            self._save()
            out = self._receipt(n, when, msg, before["kind"], a, [], lines, None)
            return out | {"held": False, "released": [], "held_count": len(self.held), "duplicate_of": before["n"]}

        record = self._check(text, name)
        if record and before:              # same control ID, different message: a numbering fault
            record = record | {"issues": record["issues"] + [REUSED]}
        issues = record["issues"] if record else []
        code, words = self._verdict(record)

        held = False
        if record and record["kind"] == "lab-report" and any(i["message_id"] == ORPHAN for i in issues):
            kept = text.strip("\r\n")
            if any(h["text"] == kept for h in self.held):
                held, words = True, "Held already: " + words
            elif len(self.held) < MAX_HELD:
                self.held.append({"text": kept, "name": name, "n": n, "control_id": msg.control_id,
                                  "held_since": when.isoformat(timespec="seconds")})
                held, words = True, "Held: " + words
            else:
                words += f"; not held (queue full at {MAX_HELD})"
        a = acks.build(code, words, ack_id, when, msg)

        released = []
        if record and record["kind"] == "patient" and record["resources"]:
            # A message sent again unchanged is the same registration: remember it once, so
            # repeats (a resend, or someone clicking repeatedly) never grow what each message reads.
            kept = text.strip("\r\n")
            if kept not in self._seen:
                self._seen.add(kept)
                self.registered.append(kept)
                released = self._release(when, _ids(text))
        if key:
            self.seen.pop(key, None)
            self.seen[key] = {"n": n, "code": code, "kind": record["kind"] if record else "unreadable", "digest": digest}
            while len(self.seen) > MAX_SEEN:
                self.seen.pop(next(iter(self.seen)))
        self._save()
        out = self._receipt(n, when, msg, record["kind"] if record else "unreadable", a, issues, lines, record)
        return out | {"held": held, "released": released, "held_count": len(self.held), "duplicate_of": None}

    def _check(self, text: str, name: str) -> dict | None:
        """The gateway's own checks on one message, against the registrations accepted so far."""
        context = [(REGISTRATIONS, "\n".join(self.registered))] if self.registered else []
        result = bridge.inspect(text, name, context, self.work)
        return result["records"][0] if result["records"] else None

    def _verdict(self, record: dict | None) -> tuple[str, str]:
        issues = record["issues"] if record else []
        errors = [i for i in issues if i["category"] == "error"]
        warnings = [i for i in issues if i["category"] == "warning"]
        if record is None or record["kind"] in ("unsupported", "unparseable"):
            reason = (errors or warnings or [{"label": "Message could not be processed"}])[0]
            return acks.REJECT, self._words(reason)
        if errors:
            return acks.ERROR, self._words(errors[0]) + (f" (+{len(errors) - 1} more)" if len(errors) > 1 else "")
        return acks.ACCEPT, "Accepted" + (f" with {len(warnings)} warning{'s' if len(warnings) > 1 else ''}"
                                          if warnings else "")

    def _release(self, when: datetime, ids: set[str]) -> list[dict]:
        """Try again the held reports that share an identifier with the new registration; file
        those that now match a patient. Each keeps its own message number, so its record id is
        the one it had when it arrived."""
        released, still = [], []
        for h in self.held:
            if not ids & _ids(h["text"]):
                still.append(h)
                continue
            record = self._check(h["text"], h["name"])
            if record and not any(i["message_id"] == ORPHAN for i in record["issues"]):
                code, words = self._verdict(record)
                released.append({
                    "n": h["n"], "control_id": h["control_id"], "held_since": h["held_since"],
                    "released": when.isoformat(timespec="seconds"), "kind": record["kind"],
                    "code": code, "code_name": acks.CODE_NAMES[code], "text": words,
                    "issues": record["issues"], "lines": text_lines(h["text"]),
                    "record_id": record["record_id"], "linked_patient": record.get("linked_patient"),
                    "resources": len(record["resources"]),
                })
            else:
                still.append(h)
        self.held = still
        return released

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
