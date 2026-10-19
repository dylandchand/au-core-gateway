"""The end-to-end run: HL7 v2 files -> canonical -> AU Core -> validator -> report.

Stages, each talking to the next only through its output type:
  1. ingest   parse every file, route each message by type:
                ADT (registration/update) -> PatientRecord   (patient_adapter)
                ORU^R01 (lab results)     -> LabReport       (lab_adapter)
  2. mapping  PatientRecord -> AU Core Patient + trace       (patient_builder)
  3. validate every Patient through the official validator in one run
  4. link     every LabReport to its registered patient (IHI, then MRN, then Medicare);
              no match = orphan result; a match with different name/birth date = warning
  5. report   issues joined back to source fields through the trace

Lab results are linked and checked but not yet converted to FHIR (that is Phase 3).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from gateway.ingest.hl7v2 import HL7ParseError, Message, parse_file
from gateway.ingest.lab_adapter import extract_lab_report
from gateway.ingest.patient_adapter import extract_patient, load_rules
from gateway.mapping.patient_builder import build_patient, locate
from gateway.model.canonical import IngestIssue, LabReport, SourceRef
from gateway.validate import validator

PATIENT_EVENTS = {"ADT^A01", "ADT^A04", "ADT^A08", "ADT^A28", "ADT^A31"}
LAB_EVENTS = {"ORU^R01"}
LINK_ORDER = ("ihi", "mrn", "medicare")      # most to least trustworthy
CATEGORIES = ("error", "could-not-check", "warning", "info")


def _issue_dict(issue: IngestIssue, stage: str) -> dict:
    return {"stage": stage, "severity": issue.severity,
            "category": "error" if issue.severity == "error" else "warning",
            "message_id": issue.code, "expression": "", "message": issue.message,
            "source": str(issue.source), "source_locator": issue.source.locator}


def _new_entry(kind: str, record_id: str, file: str, control_id: str) -> dict:
    return {"kind": kind, "record_id": record_id, "source_file": file,
            "message_control_id": control_id, "resource": None, "issues": []}


def _ingest(files: list[Path], rules: dict, resources_dir: Path):
    """Stage 1 + 2: parse, route, build Patients. Returns entries, traces, lab reports."""
    entries: list[dict] = []
    traces: dict[str, dict[str, SourceRef]] = {}
    patients: list[tuple[dict, dict]] = []      # (entry, built Patient resource)
    labs: list[tuple[dict, LabReport]] = []
    for path in files:
        try:
            messages: list[Message] = parse_file(path)
        except HL7ParseError as exc:
            entry = _new_entry("unparseable", path.stem, path.name, "")
            entry["issues"].append(_issue_dict(IngestIssue(
                "error", "ingest.parse-error", str(exc), SourceRef(path.name, exc.line, "")), "ingest"))
            entries.append(entry)
            continue
        for msg in messages:
            control_id = msg.control_id
            record_id = f"{path.stem}-{control_id}"
            if msg.message_type in PATIENT_EVENTS:
                entry = _new_entry("patient", record_id, path.name, control_id)
                rec, issues = extract_patient(msg, "", rules)
                entry["issues"] += [_issue_dict(i, "ingest") for i in issues]
                if rec is not None:
                    patient, trace, map_issues = build_patient(rec, rules)
                    entry["issues"] += [_issue_dict(i, "mapping") for i in map_issues]
                    resource_file = f"{patient['id']}.json"
                    (resources_dir / resource_file).write_text(json.dumps(patient, indent=2))
                    entry["resource"] = resource_file
                    traces[resource_file] = trace
                    patients.append((entry, patient))
            elif msg.message_type in LAB_EVENTS:
                entry = _new_entry("lab-report", record_id, path.name, control_id)
                report, issues = extract_lab_report(msg, rules)
                entry["issues"] += [_issue_dict(i, "ingest") for i in issues]
                if report is not None:
                    entry["result_count"] = len(report.results)
                    # Results are checked at ingest but not yet converted to FHIR (Phase 3).
                    # Linking needs only the patient details, so the results are released here:
                    # at 1,000+ patients there are ~400k of them.
                    report.results = []
                    labs.append((entry, report))
            else:
                entry = _new_entry("unsupported", record_id, path.name, control_id)
                entry["issues"].append(_issue_dict(IngestIssue(
                    "warning", "ingest.unsupported-message",
                    f"message type {msg.message_type!r} is not handled; skipped",
                    SourceRef(path.name, msg.msh.line, "MSH-9")), "ingest"))
            entries.append(entry)
    return entries, traces, patients, labs


def _link(patients: list[tuple[dict, dict]], labs: list[tuple[dict, LabReport]]) -> None:
    """Stage 4: attach every lab report to a registered patient, or flag it."""
    index: dict[tuple[str, str], dict] = {}
    for entry, patient in patients:
        for ident in patient.get("identifier", []):
            kind = {"NI": "ihi", "MR": "mrn", "MC": "medicare"}.get(ident["type"]["coding"][0]["code"])
            if kind and ident.get("value"):
                index.setdefault((kind, ident["value"]), {"entry": entry, "patient": patient})

    for entry, report in labs:
        by_kind = {i.kind: i for i in report.patient_identifiers if i.value.value}
        match, used = None, None
        for kind in LINK_ORDER:
            if kind in by_kind and (kind, by_kind[kind].value.value) in index:
                match, used = index[(kind, by_kind[kind].value.value)], by_kind[kind]
                break
        if match is None:
            entry["linked_patient"] = None
            entry["issues"].append(_issue_dict(IngestIssue(
                "error", "link.orphan-result",
                "no registered patient matches this report's identifiers "
                f"({', '.join(sorted(by_kind)) or 'none'})",
                report.identifiers_source or SourceRef(report.source_file, 0, "PID-3")), "link"))
            continue
        patient = match["patient"]
        entry["linked_patient"] = match["entry"]["record_id"]
        entry["linked_by"] = used.kind
        registered_family = (patient.get("name") or [{}])[0].get("family", "")
        if report.patient_family and report.patient_family.value \
                and report.patient_family.value.casefold() != registered_family.casefold():
            entry["issues"].append(_issue_dict(IngestIssue(
                "warning", "link.demographic-mismatch",
                f"family name {report.patient_family.value!r} differs from registered "
                f"{registered_family!r} for the same {used.kind.upper()}",
                report.patient_family.source), "link"))
        if report.patient_birth_date and report.patient_birth_date.value \
                and report.patient_birth_date.value != patient.get("birthDate"):
            entry["issues"].append(_issue_dict(IngestIssue(
                "warning", "link.demographic-mismatch",
                f"birth date {report.patient_birth_date.value} differs from registered "
                f"{patient.get('birthDate')} for the same {used.kind.upper()}",
                report.patient_birth_date.source), "link"))


def run(inputs: list[Path], source: str, out_dir: Path) -> dict:
    rules = load_rules(source)
    resources_dir = out_dir / "resources"
    resources_dir.mkdir(parents=True, exist_ok=True)
    for old in resources_dir.glob("*.json"):
        old.unlink()

    files = sorted(p for i in inputs for p in ([i] if i.is_file() else i.rglob("*.hl7")))
    started = datetime.now(timezone.utc)
    entries, traces, patients, labs = _ingest(files, rules, resources_dir)

    validated_at = datetime.now(timezone.utc)
    results = validator.validate_folder(resources_dir) if traces else {}
    for entry in entries:
        file = entry["resource"]
        for issue in results.get(file, []) if file else []:
            ref = locate(traces[file], issue.expression, issue.message, issue.message_id)
            entry["issues"].append({"stage": "validator", **issue.to_dict(),
                                    "source": str(ref) if ref else "",
                                    "source_locator": ref.locator if ref else ""})
    _link(patients, labs)
    finished = datetime.now(timezone.utc)

    for entry in entries:
        cats = [i["category"] for i in entry["issues"]]
        entry["status"] = "fail" if "error" in cats else "pass"
        entry["counts"] = {c: cats.count(c) for c in CATEGORIES}

    def kind(k: str) -> list[dict]:
        return [e for e in entries if e["kind"] == k]

    report = {
        "generated": finished.isoformat(timespec="seconds"),
        "source_rules": source,
        "validator": validator.validator_version(),
        "versions": validator.config()["packages"],
        "terminology_server": validator.config()["terminology"]["server"],
        "package_substitutions": validator.substitutions(),
        "timing_seconds": {"ingest_and_mapping": round((validated_at - started).total_seconds(), 1),
                           "validation_and_linking": round((finished - validated_at).total_seconds(), 1)},
        "summary": {
            "files": len(files),
            "records": len(entries),
            "passed": sum(e["status"] == "pass" for e in entries),
            "failed": sum(e["status"] == "fail" for e in entries),
            "patients": {"total": len(kind("patient")),
                         "passed": sum(e["status"] == "pass" for e in kind("patient"))},
            "lab_reports": {"total": len(kind("lab-report")),
                            "linked": sum(bool(e.get("linked_patient")) for e in kind("lab-report")),
                            "orphans": sum(e.get("linked_patient") is None and "result_count" in e
                                           for e in kind("lab-report")),
                            "results": sum(e.get("result_count", 0) for e in kind("lab-report"))},
            **{c: sum(e["counts"][c] for e in entries) for c in CATEGORIES},
        },
        "records": entries,
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=1))
    return report


def print_summary(report: dict, max_failures: int = 25) -> None:
    s = report["summary"]
    p, lab = s["patients"], s["lab_reports"]
    print(f"\nFiles: {s['files']}   records: {s['records']}   passed: {s['passed']}   failed: {s['failed']}")
    print(f"Patients:    {p['passed']}/{p['total']} conform to AU Core")
    print(f"Lab reports: {lab['linked']}/{lab['total']} linked to a registered patient "
          f"({lab['results']} results, {lab['orphans']} orphan{'' if lab['orphans'] == 1 else 's'})")
    print(f"Issues:      {s['error']} errors, {s['could-not-check']} could-not-check, "
          f"{s['warning']} warnings, {s['info']} info")
    t = report["timing_seconds"]
    print(f"Time:        {t['ingest_and_mapping']}s ingest + mapping, "
          f"{t['validation_and_linking']}s validation + linking")
    if report["package_substitutions"]:
        print(f"NOTE: {len(report['package_substitutions'])} package substitutions in effect "
              "(offline bootstrap). Do not publish these numbers.")
    failing = [e for e in report["records"] if e["status"] == "fail"]
    passing = [e for e in report["records"] if e["status"] == "pass"]
    print()
    for e in failing[:max_failures]:
        print(f"  [FAIL] {e['record_id']}")
        for i in e["issues"]:
            if i["category"] == "error":
                where = i["source"] or i["expression"]
                print(f"         {where:<34} {i['message_id'].split('#')[-1]}: {i['message'][:90]}")
    if len(failing) > max_failures:
        print(f"  ... and {len(failing) - max_failures} more failing records (see report.json)")
    if len(passing) <= 10:
        for e in passing:
            print(f"  [PASS] {e['record_id']}")
    else:
        print(f"  [PASS] {len(passing)} records")
