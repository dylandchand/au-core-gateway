"""Which sender's rules apply to a message, decided by who sent it.

Real interface engines route on the sending application and facility (MSH-3, MSH-4): two labs
can put the same thing in different places or code it differently, and the receiver must know
which conventions a message follows before reading it. Each rules file names its sender
(gateway/mapping/rules/<source>.yaml, `sender:`). A message from a sender no rules file names is
reported (ingest.unknown-sender at MSH-3) and not converted: reading it with another sender's
rules would be a guess.

A run can instead force one rules file for every message (`source="demo-lab"`), as before
routing existed: useful for a feed known to come from one sender. AUTO routes by sender.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from gateway.ingest.hl7v2 import Message
from gateway.model.canonical import IngestIssue, SourceRef

RULES_DIR = Path(__file__).resolve().parents[1] / "mapping" / "rules"
AUTO = "auto"
UNKNOWN_SENDER = "ingest.unknown-sender"


def load_rules(source: str) -> dict:
    return yaml.safe_load((RULES_DIR / f"{source}.yaml").read_text())


def senders() -> dict[tuple[str, str], str]:
    """(MSH-3, MSH-4) -> rules file name, for every rules file that names its sender. Two files
    naming the same sender would make routing ambiguous, so that is refused."""
    out: dict[tuple[str, str], str] = {}
    for path in sorted(RULES_DIR.glob("*.yaml")):
        sender = (yaml.safe_load(path.read_text()) or {}).get("sender")
        if not sender:
            continue
        key = (sender["application"], sender["facility"])
        if key in out:
            raise ValueError(f"rules files {out[key]} and {path.stem} both claim sender {key}")
        out[key] = path.stem
    return out


def sender_of(msg: Message) -> tuple[str, str]:
    """MSH-3 and MSH-4, first component (HD.1, the namespace id)."""
    return msg.msh.value(3), msg.msh.value(4)


class Router:
    """Rules for each message: the forced file, or the one naming the message's sender."""

    def __init__(self, source: str = AUTO):
        self.source = source
        self._loaded: dict[str, dict] = {}
        self._senders = senders() if source == AUTO else {}
        self.used: list[str] = []            # rules files actually applied, in first-use order

    def _rules(self, name: str) -> dict:
        if name not in self._loaded:
            self._loaded[name] = load_rules(name)
        if name not in self.used:
            self.used.append(name)
        return self._loaded[name]

    def rules_for(self, msg: Message) -> tuple[str, dict] | tuple[None, IngestIssue]:
        """(rules file name, rules), or (None, the issue saying no rules file applies)."""
        if self.source != AUTO:
            return self.source, self._rules(self.source)
        app, facility = sender_of(msg)
        name = self._senders.get((app, facility))
        if name is None:
            known = ", ".join(f"{a} at {f}" for a, f in sorted(self._senders)) or "none"
            return None, IngestIssue(
                "error", UNKNOWN_SENDER,
                f"no rules file for sender {app or '(blank)'!r} at facility {facility or '(blank)'!r} "
                f"(known: {known}); not converted rather than read with another sender's rules",
                SourceRef(msg.file, msg.msh.line, "MSH-3"))
        return name, self._rules(name)

    def describe(self) -> str:
        """What the report records as the run's rules."""
        if self.source != AUTO:
            return self.source
        return (", ".join(self.used) or "none") + " (chosen per message by sender)"
