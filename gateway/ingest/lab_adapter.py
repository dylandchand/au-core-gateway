"""HL7 v2 ORU^R01 -> canonical LabReport, driven by the source rules file.

Like the patient adapter, this knows field positions and HL7 conventions but nothing about
FHIR. It checks what can only be judged on the input (a numeric result that is not a number,
an impossible date), and passes everything else through as received.

Locators for repeated segments use 1-based occurrence numbers: OBX(3)-5 is field 5 of the
third OBX in the message.
"""

from __future__ import annotations

import re
from pathlib import Path

from gateway.ingest.dates import hl7_date_to_iso, hl7_ts_to_iso
from gateway.ingest.hl7v2 import Message, Segment
from gateway.identifiers.loinc import loinc_problem
from gateway.ingest.patient_adapter import extract_identifiers
from gateway.model.canonical import IngestIssue, LabReport, LabResult, SourceRef, Traced

NUMBER = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)$")


def _seg_ref(seg: Segment, occurrence: int, file: str, field: int | None = None,
             component: int | None = None) -> SourceRef:
    loc = f"{seg.name}({occurrence})"
    if field is not None:
        loc += f"-{field}"
    if component is not None:
        loc += f".{component}"
    return SourceRef(file, seg.line, loc)


def extract_lab_report(msg: Message, rules: dict) -> tuple[LabReport | None, list[IngestIssue]]:
    file = msg.file
    issues: list[IngestIssue] = []
    r = rules["lab_results"]
    pid = msg.first("PID")
    if pid is None:
        issues.append(IngestIssue("error", "ingest.no-pid", "lab report has no PID segment",
                                  SourceRef(file, msg.msh.line, "PID")))
        return None, issues

    control_id = msg.control_id or f"line{msg.msh.line}"
    report = LabReport(record_id=f"{Path(file).stem}-{control_id}", source_file=file,
                       message_control_id=control_id)
    report.patient_identifiers, report.identifiers_source, id_issues = extract_identifiers(pid, rules, file)
    issues += id_issues
    # Kept to cross-check against the registered patient (catches results filed to the
    # wrong person when an identifier is shared or mistyped).
    report.patient_family = Traced(pid.value(5, 1, 1), SourceRef(file, pid.line, "PID-5[1].1"))
    report.patient_birth_date = Traced(hl7_date_to_iso(pid.value(7)) or "", SourceRef(file, pid.line, "PID-7"))

    obr = msg.first("OBR")
    if obr is not None:
        report.report_id = Traced(obr.value(r["report_id"]), _seg_ref(obr, 1, file, r["report_id"]))
        report.ordered_test = Traced(obr.value(r["ordered_test"]),
                                     _seg_ref(obr, 1, file, r["ordered_test"]))

    obx_fields = r["obx"]
    for n, obx in enumerate(msg.all("OBX"), start=1):
        def traced(key: str, component: int = 1) -> Traced:
            f = obx_fields[key]
            return Traced(obx.value(f, 1, component), _seg_ref(obx, n, file, f, component if component > 1 else None))

        value_type = traced("value_type")
        value = traced("value")
        # Only single values are supported. A repeated or multi-part value (5.6~6.1, or
        # Positive^see note) is reported and not converted: keeping one part would change it.
        raw_value = obx.repeats(obx_fields["value"])
        if len(raw_value) > 1 or any(obx.enc.component in rv for rv in raw_value):
            issues.append(IngestIssue("error", "ingest.unsupported-value",
                                      f"result value {obx.field(obx_fields['value'])!r} has several parts; "
                                      "it is not converted rather than cut short", value.source))
        effective_raw = traced("effective")
        effective, tz_missing = hl7_ts_to_iso(effective_raw.value) if effective_raw.value else ("", False)

        if value_type.value in r["numeric_types"] and value.value and not NUMBER.match(value.value):
            issues.append(IngestIssue("error", "ingest.bad-numeric",
                                      f"result type {value_type.value} but value {value.value!r} is not a number",
                                      value.source))
        if effective is None:
            issues.append(IngestIssue("error", "ingest.bad-datetime",
                                      f"result time {effective_raw.value!r} is not a valid HL7 date/time",
                                      effective_raw.source))
            effective = ""
        elif tz_missing:
            issues.append(IngestIssue("warning", "ingest.datetime-no-timezone",
                                      f"result time {effective_raw.value!r} has a time but no timezone; "
                                      "kept as a date only", effective_raw.source))
        code = traced("code", 1)
        code_system = traced("code", 3)
        if not code.value:
            issues.append(IngestIssue("error", "ingest.missing-code", "result has no test code",
                                      code.source))
        elif code_system.value in r.get("loinc_coding_systems", []):
            problem = loinc_problem(code.value)
            if problem:
                issues.append(IngestIssue("error", "ingest.bad-loinc-code", problem, code.source))

        report.results.append(LabResult(
            set_id=obx.value(1) or str(n),
            value_type=value_type, code=code, display=traced("code", 2),
            code_system=code_system, value=value, units=traced("units", 1),
            units_system=traced("units", 3),
            status=traced("status"), effective=Traced(effective, effective_raw.source),
            segment=_seg_ref(obx, n, file)))

    if not report.results:
        issues.append(IngestIssue("error", "ingest.no-results", "lab report has no OBX results",
                                  SourceRef(file, (obr or msg.msh).line, "OBX")))
    return report, issues
