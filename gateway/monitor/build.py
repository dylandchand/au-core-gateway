"""Builds the Interface Monitor: a page that replays a synthetic lab feed through the gateway,
one message at a time, in the visitor's browser, answering each with an HL7 acknowledgement.

The page runs the same code as the local MLLP listener (gateway/interface/session.py) on
Pyodide; only the network is simulated. Everything it shows is synthetic:
  - the feed: the built-in example registrations, then a slice of the Synthea-generated feed
    (registrations, each followed by some of that patient's lab reports, in arrival order),
    always including one planted message per fault type the gateway catches itself (from the
    fault ledger) and one unsupported message type, each labelled as planted;
  - the staged faults: the project's own fault fixtures (fixtures/faults/), plus one message
    made from a clean example with an unsupported message type, and two network conditions
    that are simulated in the page because nothing real can be disconnected.

Usage:  python -m gateway monitor --feed data/synthea-1000-faulted --out out/monitor
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from gateway import provenance, site
from gateway.inspector import build as inspector
from gateway.interface.mllp import split_messages

ROOT = Path(__file__).resolve().parents[2]
PAGE = Path(__file__).resolve().parent / "page.html"
FIXTURES = ROOT / "fixtures"
SECTIONS = [("feed", "Live feed", None), ("faults", "Fault guide", None), ("data", "The data", None),
            ("about", "About this page", None)]
PATIENTS, LABS_PER_PATIENT = 30, 3
OWN_CHECKS = ("ingest.", "mapping.", "link.")    # issue ids the gateway raises itself (not the validator)
UNSUPPORTED_AT = 0.5                              # where in the feed the unsupported message arrives


def _pid3(text: str) -> str | None:
    """The IHI in PID-3 (the repeat whose identifier type, component 5, is NI), read as plain
    text so thousands of messages can be scanned quickly. Feeds here use the default |^~ separators."""
    for line in text.split("\r"):
        if line.startswith("PID|"):
            fields = line.split("|")
            for rep in (fields[3] if len(fields) > 3 else "").split("~"):
                comps = rep.split("^")
                if len(comps) > 4 and comps[4] == "NI":
                    return comps[0]
    return None


def _control_id(text: str) -> str:
    fields = text.split("\r", 1)[0].split("|")
    return fields[9] if len(fields) > 9 else ""


def _messages(feed_dir: Path, kind: str) -> list[dict]:
    out = []
    for f in sorted((feed_dir / kind).glob("*.hl7")):
        for t in split_messages(f.read_text(encoding="utf-8")):
            out.append({"text": t, "record_id": f"{f.stem}-{_control_id(t)}", "ihi": _pid3(t)})
    return out


def _planted(feed_dir: Path) -> tuple[dict[str, dict], set[str]]:
    """From the fault ledger (scripts/inject_faults.py): one planted message per fault type the
    gateway's own checks catch (record id -> {type, what}), and the record ids of every faulted
    message. Faults only the official validator catches are not planted, because the monitor
    acknowledges on the gateway's own checks."""
    ledger_file = feed_dir / "faults.json"
    if not ledger_file.is_file():
        return {}, set()
    ledger = json.loads(ledger_file.read_text())
    own = {t["name"]: t["what"] for t in ledger["types"]
           if any(e["id"].startswith(OWN_CHECKS) for e in t["expect"])}
    picked: dict[str, dict] = {}
    for fault in ledger["faults"]:
        if fault["fault"] in own and fault["fault"] not in {p["type"] for p in picked.values()}:
            picked[fault["record_id"]] = {"type": fault["fault"], "what": own[fault["fault"]]}
    return picked, {f["record_id"] for f in ledger["faults"]}


def feed(feed_dir: Path | None) -> list[dict]:
    """Messages in arrival order. Without a Synthea feed: the built-in examples. With one: the
    example registrations, then PATIENTS patients (each registration followed by up to
    LABS_PER_PATIENT of their lab reports). Among them, always, one planted message for each
    fault type the gateway catches itself (from the fault ledger), spread through the feed, and
    one unsupported message type half-way. Planted messages carry 'planted': what was changed."""
    examples = split_messages((FIXTURES / "golden" / "clean-patients.hl7").read_text())
    out = [{"text": t, "source": "example"} for t in examples]
    if not feed_dir or not (Path(feed_dir) / "adt").is_dir():
        return out + [{"text": t, "source": "example"}
                      for t in split_messages((FIXTURES / "golden" / "lab-results.hl7").read_text())]
    feed_dir = Path(feed_dir)
    adts, orus = _messages(feed_dir, "adt"), _messages(feed_dir, "oru")
    planted, faulted = _planted(feed_dir)
    adt_by_ihi = {}
    for m in adts:
        adt_by_ihi.setdefault(m["ihi"], m)
    labs_by_ihi: dict[str | None, list[dict]] = {}
    for m in orus:
        labs_by_ihi.setdefault(m["ihi"], []).append(m)

    def block(adt: dict | None, must: dict | None = None) -> list[dict]:
        """A registration (if any) and up to LABS_PER_PATIENT of that patient's lab reports,
        always including `must`. Other faulted messages are left out, so each planted fault
        shows exactly once and everything else is clean."""
        labs = [m for m in labs_by_ihi.get(adt["ihi"], []) if m["record_id"] not in faulted] if adt else []
        labs = labs[:LABS_PER_PATIENT - (1 if must and must is not adt else 0)]
        if must and must is not adt:
            labs.insert(min(1, len(labs)), must)
        return ([adt] if adt else []) + labs

    planted_blocks, used = [], set()
    by_id = {m["record_id"]: m for m in adts + orus}
    for record_id in planted:
        m = by_id.get(record_id)
        if m is None:
            continue
        if m in adts:
            adt = m
        else:   # a lab report: with its patient's registration, unless the fault is that there is none
            adt = adt_by_ihi.get(m["ihi"])
            if adt and adt["record_id"] in faulted:
                adt = None
        planted_blocks.append(block(adt, m))
        used.add(adt["ihi"] if adt else None)
    clean = [block(m) for m in adts if m["ihi"] not in used and m["record_id"] not in faulted]
    clean = clean[:max(0, PATIENTS - len(planted_blocks))]

    # Spread the planted blocks evenly through the clean ones; put the unsupported message half-way.
    blocks: list[list[dict]] = []
    gap = len(clean) / (len(planted_blocks) + 1) if planted_blocks else 0
    starts = {round(gap * (k + 1)): b for k, b in enumerate(planted_blocks)}
    for k, b in enumerate(clean):
        if k in starts:
            blocks.append(starts.pop(k))
        blocks.append(b)
    blocks += list(starts.values())
    unsupported = {"text": _unsupported(), "record_id": "", "ihi": None}
    blocks.insert(round(len(blocks) * UNSUPPORTED_AT), [unsupported])

    for b in blocks:
        for m in b:
            item = {"text": m["text"], "source": "synthea"}
            if m is unsupported:
                item.update(source="example", planted="message type changed to ADT^A03 (discharge), "
                                                       "which this interface does not handle")
            elif m["record_id"] in planted:
                item["planted"] = planted[m["record_id"]]["what"]
            out.append(item)
    return out


def _unsupported(control_id: str = "DEMO-A03") -> str:
    """A clean example registration with its message type changed to a discharge (ADT^A03). The
    feed's copy and the staged one carry different control IDs: they are separate messages."""
    clean = split_messages((FIXTURES / "golden" / "clean-patients.hl7").read_text())[0]
    return clean.replace("|ADT^A04^ADT_A01|", "|ADT^A03^ADT_A03|", 1).replace("|GOLD0001|", f"|{control_id}|", 1)


def _fixture(name: str) -> str:
    return split_messages((FIXTURES / "faults" / name).read_text())[0]


def _early_pair(feed_dir: Path | None, messages: list[dict]) -> tuple[str, str] | None:
    """A lab report and its patient's registration, for a synthetic patient who is not in the feed
    and has no planted mistake, so sending the report first always shows it held, then filed."""
    if not feed_dir or not (Path(feed_dir) / "adt").is_dir():
        return None
    feed_dir = Path(feed_dir)
    _, faulted = _planted(feed_dir)
    in_feed = {_pid3(m["text"]) for m in messages}
    labs: dict[str | None, list[dict]] = {}
    for m in _messages(feed_dir, "oru"):
        if m["record_id"] not in faulted:
            labs.setdefault(m["ihi"], []).append(m)
    for adt in _messages(feed_dir, "adt"):
        if adt["ihi"] not in in_feed and adt["record_id"] not in faulted and labs.get(adt["ihi"]):
            return labs[adt["ihi"]][0]["text"], adt["text"]
    return None


def scenarios(feed_dir: Path | None = None, messages: list[dict] | None = None,
              describe_only: bool = False) -> list[dict]:
    """What the page can stage (the "early" pair only with a Synthea feed: it needs a patient
    outside the feed). describe_only lists every fault for the runbook, the pair without its text. 'send' scenarios go through the real gateway; 'network' ones are
    simulated in the page. 'chip' labels it in the bar above the feed, 'short' is the guide's one-line
    summary and 'action' its button's label; steps are what a support person would check, in order."""
    unsupported = _unsupported("DEMO-A03-STAGED")
    resent = split_messages((FIXTURES / "golden" / "lab-results.hl7").read_text())[1]
    early = _early_pair(feed_dir, messages or []) or ((None, None) if describe_only else None)
    return [
        {"id": "orphan", "chip": "Unknown patient",
         "short": "PID-3 matches no registered patient", "action": "Send the unmatched lab report",
         "kind": "send", "title": "Lab report for an unknown patient",
         "what": "A lab report arrives for a patient the hospital has no registration for, and none follows. "
                 "The gateway holds it, and it stays waiting.",
         "text": _fixture("lab-orphan-result.hl7"),
         "steps": ["Read the error: the identifiers in PID-3 match no registered patient.",
                   ("Check the held queue: the report waits there, and is filed by itself if the registration "
                    "turns up (see 'Lab report before its registration')."),
                   ("If it waits too long, check whether that patient's registration failed earlier, and confirm "
                    "the identifiers with the sending lab."),
                   ("Never file the results against a best guess. Log an incident and keep the report held until "
                    "the patient is confirmed.")]},
        *([{"id": "early", "chip": "Out of order",
             "short": "PID-3 matches nobody yet", "action": "Send the report, then its registration",
             "kind": "send", "title": "Lab report before its registration",
             "what": "A lab report arrives first and its patient's registration a few seconds later, as happens when "
                     "two systems send at once. Uses a synthetic patient who is not in the feed.",
             "text": early[0], "then": early[1],
             "steps": ["The report gets AE (no registered patient) and is held, not thrown away.",
                       "When the registration is accepted, the gateway retries the held report and files it.",
                       ("Check the filed report: it is checked again on filing, so any other problem in it "
                        "still shows."),
                       ("If reports are often held, ask why registrations arrive late: the two systems may send "
                        "on different schedules.")]}] if early else []),
        {"id": "resend", "chip": "Resent",
         "short": "Same MSH-10, same content", "action": "Send a report, then send it again",
         "kind": "send", "title": "Resent after a lost acknowledgement",
         "what": "The gateway answers a lab report, but the acknowledgement is lost on the way back, so the sender "
                 "sends the same message again. Uses the project's clean example lab report.",
         "text": resent, "then": resent,
         "steps": [("The second copy has the same sender (MSH-3, MSH-4), control ID (MSH-10) and content: the "
                    "gateway answers it with the original's code and processes nothing twice."),
                   "Check the log: the resend is marked as a duplicate of the first message.",
                   ("If resends are frequent, the acknowledgements are not getting back: check the sender's "
                    "timeout and the network between the two systems."),
                   ("A control ID reused for a different message is not a duplicate: it is processed, with a "
                    "warning at MSH-10, and the sender's numbering should be fixed.")]},
        {"id": "comma", "chip": "Decimal comma",
         "short": "OBX-5 arrives as 5,4", "action": "Send the result written as 5,4",
         "kind": "send", "title": "Result with a decimal comma",
         "what": "One numeric result arrives as 5,4 instead of 5.4.",
         "text": _fixture("lab-non-numeric-value.hl7"),
         "steps": ["Read the error: it names the result and the field (OBX-5, the result value).",
                   ("Ask what changed: a decimal comma usually means a regional setting changed on the "
                    "sending system."),
                   "Contact the lab's interface team with the message's control ID.",
                   ("The result was not converted, so nothing wrong reached the record. Log the incident and "
                    "note the sender's change for change management.")]},
        {"id": "loinc", "chip": "Mistyped test code",
         "short": "OBX-3 check digit is wrong", "action": "Send the mistyped test code",
         "kind": "send", "title": "Mistyped test code",
         "what": "A result's LOINC test code has a mistyped last digit.",
         "text": _fixture("lab-bad-loinc-check-digit.hl7"),
         "steps": ["Read the error: the LOINC check digit in OBX-3 does not match the code.",
                   ("Compare the code with the lab's test catalogue; a typo in the sender's code table is the "
                    "usual cause."),
                   "Raise it with the lab so the table is corrected at the source, not patched here."]},
        {"id": "status", "chip": "Unknown status",
         "short": "OBX-11 is not in the rules", "action": "Send the unknown status code",
         "kind": "send", "title": "Unknown result status",
         "what": "A result arrives with a status code that is not in the agreed rules.",
         "text": _fixture("obs-unmapped-status.hl7"),
         "steps": ["Read the error: the status in OBX-11 is not in this lab's translation rules.",
                   "Check it against HL7 table 0085 (observation result status).",
                   ("If the lab has started sending a legitimate new status, update the rules through the "
                    "change process. If not, ask the lab to fix it.")]},
        {"id": "a03", "chip": "Unsupported type",
         "short": "MSH-9 is ADT^A03", "action": "Send a discharge message",
         "kind": "send", "title": "Unsupported message type",
         "what": "The sender starts sending discharge messages (ADT^A03), which this interface was never "
                 "set up for. Made from a clean example by changing its message type.",
         "text": unsupported,
         "steps": ["Read the rejection (AR): the message type is not one this interface accepts.",
                   "Check the interface agreement: which message types should this sender send?",
                   "Either the sender filters them out, or supporting them goes through a change request."]},
        {"id": "ihi", "chip": "Mistyped IHI",
         "short": "PID-3 IHI check digit is wrong", "action": "Send the mistyped IHI",
         "kind": "send", "title": "Accepted, but still wrong",
         "what": "A registration whose IHI has one wrong digit. The gateway's own checks accept it; "
                 "HL7's official validator, which runs separately, rejects the IHI check digit.",
         "text": _fixture("ihi-bad-check-digit.hl7"),
         "steps": ["An ACK of AA means the message was received and read, not that every value is right.",
                   ("Official validation runs as a separate step (see the Results page), and that is where "
                    "this one is caught."),
                   "Trace it to PID-3 and ask the sender to correct the IHI at the source."]},
        {"id": "unreachable", "chip": "Cut connection",
         "short": "Nothing arrives, nothing is acknowledged", "action": "Cut the connection",
         "kind": "network", "title": "Sender cannot reach the gateway",
         "what": "Simulated: the sending system is pointed at the wrong address or port, so nothing arrives "
                 "and nothing is acknowledged.",
         "steps": [("One sender or all of them? One points at that system; all points at the network or the "
                    "gateway."),
                   "Is the gateway listening? Check the service is running on the expected port.",
                   "Can the sender reach it? Check the address, the port, recent DNS or firewall changes.",
                   ("The sender will be queueing messages. Once fixed, watch the backlog drain and check "
                    "nothing was lost."),
                   "Log an incident; escalate early if results for patients are delayed."]},
        {"id": "dropped", "chip": "Drop a message",
         "short": "The end-of-message marker never arrives", "action": "Drop the next message",
         "kind": "network", "title": "Connection drops mid-message",
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
    data = {"pyodide": cfg["inspector"]["pyodide"], "feed": messages, "scenarios": scenarios(feed_dir, messages),
            "hl7": json.loads((ROOT / "gateway" / "hl7_names.json").read_text()),
            "synthea": any(m["source"] == "synthea" for m in messages)}
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/").replace("<!--", "<\\!--")
    page = site.fill(PAGE.read_text(), "monitor", SECTIONS, "",
                     "This page replays a synthetic lab feed through the gateway's own code, in your browser.")
    page = page.replace("__PYODIDE_VERSION__", cfg["inspector"]["pyodide"]).replace("__MONITOR_DATA__", payload)
    # Its feed (a Synthea slice, when given) and the examples it starts with and stages.
    feed_sources = [Path(feed_dir)] if feed_dir and (Path(feed_dir) / "adt").is_dir() else []
    sources = provenance.describe(feed_sources + [FIXTURES / "golden", FIXTURES / "faults"])
    for s in sources:
        if s["kind"] == "synthea":
            s["slice"] = {"messages": sum(1 for m in messages if m["source"] == "synthea"),
                          "planted": sum(1 for m in messages if m["source"] == "synthea" and m.get("planted"))}
    page = page.replace("__PROVENANCE__", provenance.html(sources))
    (out_dir / "index.html").write_text(page)
    return out_dir / "index.html"
