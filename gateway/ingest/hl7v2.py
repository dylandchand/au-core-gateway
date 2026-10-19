"""Minimal, dependency-free HL7 v2 (ER7, pipe-delimited) parser.

Scope for Phase 1: split files into messages and segments, address fields as HL7 does
(PID-3, repeat 2, component 1), honour the encoding characters declared in MSH-2, and
unescape \\F\\ \\S\\ \\T\\ \\R\\ \\E\\. It knows nothing about healthcare meaning; that lives
in the mapping rules.

Numbering follows HL7 conventions, which are 1-based and have one trap: in MSH the field
separator itself is MSH-1, so MSH-n is split index n-1. Every other segment's field n is
split index n.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SEGMENT_NAME = re.compile(r"^[A-Z][A-Z0-9]{2}$")


class HL7ParseError(ValueError):
    def __init__(self, message: str, line: int):
        super().__init__(f"line {line}: {message}")
        self.line = line


@dataclass(frozen=True)
class Encoding:
    field: str = "|"
    component: str = "^"
    repetition: str = "~"
    escape: str = "\\"
    subcomponent: str = "&"

    def unescape(self, text: str) -> str:
        if self.escape not in text:
            return text
        e = self.escape
        table = {f"{e}F{e}": self.field, f"{e}S{e}": self.component, f"{e}T{e}": self.subcomponent,
                 f"{e}R{e}": self.repetition, f"{e}E{e}": e}
        out, i = [], 0
        while i < len(text):
            token = text[i:i + 3]
            if token in table:
                out.append(table[token])
                i += 3
            else:
                out.append(text[i])
                i += 1
        return "".join(out)


@dataclass
class Segment:
    name: str
    raw_fields: list[str]
    line: int
    enc: Encoding

    def field(self, n: int) -> str:
        """Raw text of field n (1-based, HL7 numbering). '' when absent."""
        if self.name == "MSH" and n == 1:
            return self.enc.field          # MSH-1 is the separator itself, not in the split
        idx = n - 1 if self.name == "MSH" else n
        return self.raw_fields[idx] if 0 <= idx < len(self.raw_fields) else ""

    def repeats(self, n: int) -> list[str]:
        """Raw repetitions of field n. An empty field has no repeats."""
        raw = self.field(n)
        if self.name == "MSH" and n in (1, 2):
            return [raw] if raw else []
        return [r for r in raw.split(self.enc.repetition)] if raw else []

    def component(self, raw_repeat: str, c: int) -> str:
        """Component c (1-based) of one repetition, unescaped."""
        parts = raw_repeat.split(self.enc.component)
        return self.enc.unescape(parts[c - 1]) if 0 < c <= len(parts) else ""

    def value(self, n: int, repeat: int = 1, c: int = 1) -> str:
        reps = self.repeats(n)
        return self.component(reps[repeat - 1], c) if 0 < repeat <= len(reps) else ""


@dataclass
class Message:
    segments: list[Segment]
    file: str

    @property
    def msh(self) -> Segment:
        return self.segments[0]

    @property
    def control_id(self) -> str:
        return self.msh.value(10)

    @property
    def message_type(self) -> str:
        msh = self.msh
        return "^".join(p for p in (msh.value(9, 1, 1), msh.value(9, 1, 2)) if p)

    def first(self, name: str) -> Segment | None:
        return next((s for s in self.segments if s.name == name), None)

    def all(self, name: str) -> list[Segment]:
        return [s for s in self.segments if s.name == name]


def _encoding_from_msh(line_text: str, line_no: int) -> Encoding:
    if len(line_text) < 8 or not line_text.startswith("MSH"):
        raise HL7ParseError("message must start with an MSH segment", line_no)
    field_sep = line_text[3]
    chars = line_text[4:].split(field_sep, 1)[0]
    if len(chars) < 4:
        raise HL7ParseError(f"MSH-2 encoding characters too short: {chars!r}", line_no)
    return Encoding(field_sep, chars[0], chars[1], chars[2], chars[3] if len(chars) > 3 else "&")


def parse_text(text: str, file: str = "<memory>") -> list[Message]:
    """Split a file's text into messages. Segments may end in \\r, \\n or \\r\\n."""
    messages: list[Message] = []
    current: list[Segment] = []
    enc: Encoding | None = None
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for line_no, line_text in enumerate(lines, start=1):
        line_text = line_text.rstrip()
        if not line_text:
            continue
        if line_text.startswith("MSH"):
            if current:
                messages.append(Message(current, file))
            enc = _encoding_from_msh(line_text, line_no)
            current = []
        elif enc is None:
            raise HL7ParseError(f"segment {line_text[:3]!r} before any MSH", line_no)
        name = line_text[:3]
        if not SEGMENT_NAME.match(name) or (len(line_text) > 3 and line_text[3] != enc.field):
            raise HL7ParseError(f"not a segment: {line_text[:20]!r}", line_no)
        current.append(Segment(name, line_text.split(enc.field), line_no, enc))
    if current:
        messages.append(Message(current, file))
    return messages


def parse_file(path: Path) -> list[Message]:
    return parse_text(path.read_text(encoding="utf-8"), file=path.name)
