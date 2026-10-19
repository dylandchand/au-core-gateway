"""HL7 v2 PID segment -> canonical PatientRecord, driven by a source rules file.

This module knows HL7 v2 field positions (via the rules) but nothing about FHIR.
Values are kept raw (as received) apart from date checking; translation into FHIR codes
happens in the FHIR builder so every source shares one builder.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import yaml

from gateway.ingest.hl7v2 import Message, Segment
from gateway.model.canonical import (Address, Identifier, IngestIssue, Name, PatientRecord,
                                     SourceRef, Traced)

RULES_DIR = Path(__file__).resolve().parent.parent / "mapping" / "rules"
HL7_DATE = re.compile(r"^(\d{4})(\d{2})?(\d{2})?(\d{4}(\d{2}(\.\d+)?)?)?([+-]\d{4})?$")


def load_rules(source: str) -> dict:
    return yaml.safe_load((RULES_DIR / f"{source}.yaml").read_text())


def _field_no(spec: str) -> int:
    """'PID-7' -> 7"""
    return int(spec.split("-", 1)[1])


def _ref(seg: Segment, field: int, repeat: int | None = None, component: int | None = None) -> SourceRef:
    loc = f"{seg.name}-{field}"
    if repeat is not None:
        loc += f"[{repeat}]"
    if component is not None:
        loc += f".{component}"
    return SourceRef(file="", line=seg.line, locator=loc)


def _with_file(ref: SourceRef, file: str) -> SourceRef:
    return SourceRef(file=file, line=ref.line, locator=ref.locator)


def hl7_date_to_fhir(raw: str) -> str | None:
    """HL7 TS -> FHIR date (YYYY, YYYY-MM or YYYY-MM-DD). None if not a real date.
    Partial dates stay partial: '1980' never becomes '1980-01-01'."""
    m = HL7_DATE.match(raw)
    if not m:
        return None
    year, month, day = m.group(1), m.group(2), m.group(3)
    try:
        if day:
            date(int(year), int(month), int(day))
            return f"{year}-{month}-{day}"
        if month:
            if not 1 <= int(month) <= 12:
                return None
            return f"{year}-{month}"
        return year
    except ValueError:
        return None


def extract_patient(msg: Message, source: str, rules: dict) -> tuple[PatientRecord | None, list[IngestIssue]]:
    file = msg.file
    issues: list[IngestIssue] = []
    pid = msg.first("PID")
    if pid is None:
        issues.append(IngestIssue("error", "ingest.no-pid", "message has no PID segment",
                                  SourceRef(file, msg.msh.line, "PID")))
        return None, issues

    r = rules["patient"]
    control_id = msg.control_id or f"line{msg.msh.line}"
    rec = PatientRecord(record_id=f"{Path(file).stem}-{control_id}", source_file=file,
                        message_control_id=control_id)

    def traced(seg: Segment, field: int, repeat: int | None, component: int, raw: str) -> Traced:
        return Traced(raw, _with_file(_ref(seg, field, repeat, component if repeat else None), file))

    # Identifiers: PID-3 (CX), one canonical identifier per repetition that matches a rule.
    f = _field_no(r["identifiers"]["field"])
    rec.identifiers_source = _with_file(_ref(pid, f), file)
    for i, rep in enumerate(pid.repeats(f), start=1):
        value, authority, type_code = (pid.component(rep, 1), pid.component(rep, 4),
                                       pid.component(rep, 5))
        rule = next((ru for ru in r["identifiers"]["rules"]
                     if ru["when"].get("type", type_code) == type_code
                     and ru["when"].get("authority", authority) == authority), None)
        if rule is None:
            issues.append(IngestIssue("warning", "ingest.unmapped-identifier",
                                      f"no rule for identifier type {type_code!r} authority {authority!r}; skipped",
                                      _with_file(_ref(pid, f, i), file)))
            continue
        ident = Identifier(kind=rule["kind"], value=Traced(value, _with_file(_ref(pid, f, i), file)),
                           type_code=type_code, authority=authority)
        rec.identifiers.append(ident)

    # Name: PID-5 (XPN)
    f = _field_no(r["name"]["field"])
    rec.names_source = _with_file(_ref(pid, f), file)
    for i, rep in enumerate(pid.repeats(f), start=1):
        given = [traced(pid, f, i, c, pid.component(rep, c)) for c in (2, 3)
                 if pid.component(rep, c)]
        use = r["name"]["use"].get(pid.component(rep, 7), r["name"]["use"][""])
        rec.names.append(Name(family=traced(pid, f, i, 1, pid.component(rep, 1)), given=given, use=use))

    # Birth date: PID-7 (TS). Checked here because an impossible date is an input problem,
    # not a FHIR conformance problem.
    f = _field_no(r["birth_date"]["field"])
    raw = pid.value(f)
    ref = _with_file(_ref(pid, f), file)
    iso = hl7_date_to_fhir(raw) if raw else ""
    if iso is None:
        issues.append(IngestIssue("error", "ingest.bad-date",
                                  f"birth date {raw!r} is not a valid HL7 date (YYYY[MM[DD]])", ref))
        iso = ""
    rec.birth_date = Traced(iso, ref)  # canonical dates are ISO 8601, possibly partial

    # Sex: PID-8 (IS, table 0001), kept raw.
    f = _field_no(r["sex"]["field"])
    rec.sex = Traced(pid.value(f), _with_file(_ref(pid, f), file))

    # Address: PID-11 (XAD)
    f = _field_no(r["address"]["field"])
    rec.addresses_source = _with_file(_ref(pid, f), file)
    for i, rep in enumerate(pid.repeats(f), start=1):
        lines = [traced(pid, f, i, c, pid.component(rep, c)) for c in (1, 2) if pid.component(rep, c)]
        use = r["address"]["use"].get(pid.component(rep, 7), r["address"]["use"][""])
        rec.addresses.append(Address(
            lines=lines,
            city=traced(pid, f, i, 3, pid.component(rep, 3)),
            state=traced(pid, f, i, 4, pid.component(rep, 4)),
            postcode=traced(pid, f, i, 5, pid.component(rep, 5)),
            country=traced(pid, f, i, 6, pid.component(rep, 6)),
            use=use))
    return rec, issues
