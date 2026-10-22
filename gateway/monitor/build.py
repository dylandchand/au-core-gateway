"""Builds the Interface Monitor: a page that replays a synthetic lab feed through the gateway,
one message at a time, in the visitor's browser, answering each with an HL7 acknowledgement.

The page runs the same code as the local MLLP listener (gateway/interface/session.py) on
Pyodide; only the network is simulated. Everything it shows is synthetic:
  - the feed: the built-in example registrations, then a slice of the Synthea-generated feed
    (registrations, each followed by some of that patient's lab reports, in arrival order);
  - the staged faults: the project's own fault fixtures (fixtures/faults/), plus one message
    made from a clean example with an unsupported message type, and two network conditions
    that are simulated in the page because nothing real can be disconnected.

Usage:  python -m gateway monitor --feed data/synthea-1000-faulted --out out/monitor
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from gateway import site
from gateway.ingest.hl7v2 import parse_text
from gateway.inspector import build as inspector
from gateway.interface.mllp import split_messages

ROOT = Path(__file__).resolve().parents[2]
PAGE = Path(__file__).resolve().parent / "page.html"
FIXTURES = ROOT / "fixtures"
SECTIONS = [("feed", "Live feed", None), ("faults", "Stage a fault", None), ("about", "About this page", None)]
PATIENTS, LABS_PER_PATIENT = 30, 3


def _ihi(text: str) -> str | None:
    pid = parse_text(text)[0].first("PID")
    if pid is None:
        return None
    for rep, raw in enumerate(pid.repeats(3), start=1):
        if pid.value(3, rep, 5) == "NI":
            return pid.value(3, rep, 1)
    return None


def feed(feed_dir: Path | None) -> list[dict]:
    """Messages in arrival order: the example registrations, then per patient a registration and
    up to LABS_PER_PATIENT of their lab reports. Without a Synthea feed, the built-in examples."""
    examples = split_messages((FIXTURES / "golden" / "clean-patients.hl7").read_text())
    out = [{"text": t, "source": "example"} for t in examples]
    if not feed_dir or not (Path(feed_dir) / "adt").is_dir():
        return out + [{"text": t, "source": "example"}
                      for t in split_messages((FIXTURES / "golden" / "lab-results.hl7").read_text())]
    adts = []
    for f in sorted((Path(feed_dir) / "adt").glob("*.hl7")):
        adts += split_messages(f.read_text(encoding="utf-8"))
        if len(adts) >= PATIENTS:
            break
    adts = adts[:PATIENTS]
    by_ihi = {_ihi(t): [] for t in adts}
    for f in sorted((Path(feed_dir) / "oru").glob("*.hl7")):
        for t in split_messages(f.read_text(encoding="utf-8")):
            labs = by_ihi.get(_ihi(t))
            if labs is not None and len(labs) < LABS_PER_PATIENT:
                labs.append(t)
        if all(len(v) >= LABS_PER_PATIENT for v in by_ihi.values()):
            break
    for t in adts:
        out.append({"text": t, "source": "synthea"})
        out += [{"text": lab, "source": "synthea"} for lab in by_ihi[_ihi(t)]]
    return out


def _fixture(name: str) -> str:
    return split_messages((FIXTURES / "faults" / name).read_text())[0]


def scenarios() -> list[dict]:
    """What the page can stage. 'send' scenarios go through the real gateway; 'network' ones are
    simulated in the page. 'short' is the list's one-line summary, 'action' the button's label; steps are what a support person
    would check, in order."""
    clean = split_messages((FIXTURES / "golden" / "clean-patients.hl7").read_text())[0]
    unsupported = clean.replace("|ADT^A04^ADT_A01|", "|ADT^A03^ADT_A03|", 1).replace("|GOLD0001|", "|DEMO-A03|", 1)
    return [
        {"id": "orphan", "short": "PID-3 matches no registered patient", "action": "Send the unmatched lab report", "kind": "send", "title": "Lab report for an unknown patient",
         "what": "A lab report arrives for a patient the hospital has no registration for.",
         "text": _fixture("lab-orphan-result.hl7"),
         "steps": ["Read the error: the identifiers in PID-3 match no registered patient.",
                   "Check whether that patient's registration failed earlier, or simply has not arrived yet "
                   "(messages can arrive out of order).",
                   "Confirm the identifiers with the sending lab.",
                   "Never file the results against a best guess. Log an incident and hold the report until "
                   "the patient is confirmed."]},
        {"id": "comma", "short": "OBX-5 arrives as 5,4", "action": "Send the result written as 5,4", "kind": "send", "title": "Result with a decimal comma",
         "what": "One numeric result arrives as 5,4 instead of 5.4.",
         "text": _fixture("lab-non-numeric-value.hl7"),
         "steps": ["Read the error: it names the result and the field (OBX-5, the result value).",
                   "Ask what changed: a decimal comma usually means a regional setting changed on the "
                   "sending system.",
                   "Contact the lab's interface team with the message's control ID.",
                   "The result was not converted, so nothing wrong reached the record. Log the incident and "
                   "note the sender's change for change management."]},
        {"id": "loinc", "short": "OBX-3 check digit is wrong", "action": "Send the mistyped test code", "kind": "send", "title": "Mistyped test code",
         "what": "A result's LOINC test code has a mistyped last digit.",
         "text": _fixture("lab-bad-loinc-check-digit.hl7"),
         "steps": ["Read the error: the LOINC check digit in OBX-3 does not match the code.",
                   "Compare the code with the lab's test catalogue; a typo in the sender's code table is the "
                   "usual cause.",
                   "Raise it with the lab so the table is corrected at the source, not patched here."]},
        {"id": "status", "short": "OBX-11 is not in the rules", "action": "Send the unknown status code", "kind": "send", "title": "Unknown result status",
         "what": "A result arrives with a status code that is not in the agreed rules.",
         "text": _fixture("obs-unmapped-status.hl7"),
         "steps": ["Read the error: the status in OBX-11 is not in this lab's translation rules.",
                   "Check it against HL7 table 0085 (observation result status).",
                   "If the lab has started sending a legitimate new status, update the rules through the "
                   "change process. If not, ask the lab to fix it."]},
        {"id": "a03", "short": "MSH-9 is ADT^A03", "action": "Send a discharge message", "kind": "send", "title": "Unsupported message type",
         "what": "The sender starts sending discharge messages (ADT^A03), which this interface was never "
                 "set up for. Made from a clean example by changing its message type.",
         "text": unsupported,
         "steps": ["Read the rejection (AR): the message type is not one this interface accepts.",
                   "Check the interface agreement: which message types should this sender send?",
                   "Either the sender filters them out, or supporting them goes through a change request."]},
        {"id": "ihi", "short": "PID-3 IHI check digit is wrong", "action": "Send the mistyped IHI", "kind": "send", "title": "Accepted, but still wrong",
         "what": "A registration whose IHI has one wrong digit. The gateway's own checks accept it; "
                 "HL7's official validator, which runs separately, rejects the IHI check digit.",
         "text": _fixture("ihi-bad-check-digit.hl7"),
         "steps": ["An ACK of AA means the message was received and read, not that every value is right.",
                   "Official validation runs as a separate step (see the Results page), and that is where "
                   "this one is caught.",
                   "Trace it to PID-3 and ask the sender to correct the IHI at the source."]},
        {"id": "unreachable", "short": "Nothing arrives, nothing is acknowledged", "action": "Cut the connection", "kind": "network", "title": "Sender cannot reach the gateway",
         "what": "Simulated: the sending system is pointed at the wrong address or port, so nothing arrives "
                 "and nothing is acknowledged.",
         "steps": ["One sender or all of them? One points at that system; all points at the network or the "
                   "gateway.",
                   "Is the gateway listening? Check the service is running on the expected port.",
                   "Can the sender reach it? Check the address, the port, recent DNS or firewall changes.",
                   "The sender will be queueing messages. Once fixed, watch the backlog drain and check "
                   "nothing was lost.",
                   "Log an incident; escalate early if results for patients are delayed."]},
        {"id": "dropped", "short": "The end-of-message marker never arrives", "action": "Drop the next message", "kind": "network", "title": "Connection drops mid-message",
         "what": "Simulated: the connection breaks before the end-of-message marker, so the gateway never "
                 "sees a complete message and sends no acknowledgement.",
         "steps": ["The sender times out waiting for the ACK and usually resends.",
                   "Check for repeats: a resent message carries the same control ID (MSH-10).",
                   "If drops keep happening, look at the network between the two systems.",
                   "Log it, with the control IDs affected."]},
    ]


def build(out_dir: Path, feed_dir: Path | None = None) -> Path:
    cfg = tomllib.loads((ROOT / "versions.toml").read_text())
    out_dir.mkdir(parents=True, exist_ok=True)
    inspector.bundle(out_dir / "gateway.zip")
    messages = feed(feed_dir)
    data = {"pyodide": cfg["inspector"]["pyodide"], "feed": messages, "scenarios": scenarios(),
            "hl7": json.loads((ROOT / "gateway" / "hl7_names.json").read_text()),
            "synthea": any(m["source"] == "synthea" for m in messages)}
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/").replace("<!--", "<\\!--")
    page = site.fill(PAGE.read_text(), "monitor", SECTIONS, "",
                     "This page replays a synthetic lab feed through the gateway's own code, in your browser.")
    page = page.replace("__PYODIDE_VERSION__", cfg["inspector"]["pyodide"]).replace("__MONITOR_DATA__", payload)
    (out_dir / "index.html").write_text(page)
    return out_dir / "index.html"
