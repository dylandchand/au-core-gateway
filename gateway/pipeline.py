"""The end-to-end run: HL7 v2 files -> canonical -> AU Core Patient -> validator -> report.

Each stage only talks to the next through its output type:
  ingest (hl7v2 + patient_adapter) -> PatientRecord
  mapping (patient_builder)        -> FHIR JSON + trace
  validate (validator)             -> issues per resource
The report joins validator issues back to source fields through the trace.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from gateway.ingest.hl7v2 import HL7ParseError, parse_file
from gateway.ingest.patient_adapter import extract_patient, load_rules
from gateway.mapping.patient_builder import build_patient, locate
from gateway.model.canonical import IngestIssue, SourceRef
from gateway.validate import validator


def _issue_dict(issue: IngestIssue, stage: str) -> dict:
    return {"stage": stage, "severity": issue.severity, "category": "error" if issue.severity == "error" else "warning",
            "message_id": issue.code, "expression": "", "message": issue.message,
            "source": str(issue.source), "source_locator": issue.source.locator}


def run(inputs: list[Path], source: str, out_dir: Path) -> dict:
    rules = load_rules(source)
    resources_dir = out_dir / "resources"
    resources_dir.mkdir(parents=True, exist_ok=True)
    for old in resources_dir.glob("*.json"):
        old.unlink()

    files = sorted(p for i in inputs for p in ([i] if i.is_file() else i.rglob("*.hl7")))
    records: list[dict] = []
    traces: dict[str, dict[str, SourceRef]] = {}

    for path in files:
        try:
            messages = parse_file(path)
        except HL7ParseError as exc:
            records.append({"record_id": path.stem, "source_file": path.name, "resource": None,
                            "issues": [_issue_dict(IngestIssue("error", "ingest.parse-error", str(exc),
                                                              SourceRef(path.name, exc.line, "")), "ingest")]})
            continue
        for msg in messages:
            rec, ingest_issues = extract_patient(msg, source, rules)
            entry = {"record_id": rec.record_id if rec else f"{path.stem}-{msg.control_id}",
                     "source_file": path.name, "message_control_id": msg.control_id,
                     "resource": None,
                     "issues": [_issue_dict(i, "ingest") for i in ingest_issues]}
            if rec is not None:
                patient, trace, map_issues = build_patient(rec, rules)
                entry["issues"] += [_issue_dict(i, "mapping") for i in map_issues]
                resource_file = f"{patient['id']}.json"
                (resources_dir / resource_file).write_text(json.dumps(patient, indent=2))
                entry["resource"] = resource_file
                traces[resource_file] = trace
            records.append(entry)

    results = validator.validate_folder(resources_dir) if traces else {}

    for entry in records:
        file = entry["resource"]
        if not file:
            continue
        for issue in results.get(file, []):
            ref = locate(traces[file], issue.expression, issue.message, issue.message_id)
            entry["issues"].append({"stage": "validator", **issue.to_dict(),
                                    "source": str(ref) if ref else "",
                                    "source_locator": ref.locator if ref else ""})

    for entry in records:
        cats = [i["category"] for i in entry["issues"]]
        entry["status"] = "fail" if "error" in cats else "pass"
        entry["counts"] = {c: cats.count(c) for c in ("error", "could-not-check", "warning", "info")}

    report = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_rules": source,
        "validator": validator.validator_version(),
        "versions": validator.config()["packages"],
        "terminology_server": validator.config()["terminology"]["server"],
        "package_substitutions": validator.substitutions(),
        "summary": {
            "records": len(records),
            "passed": sum(e["status"] == "pass" for e in records),
            "failed": sum(e["status"] == "fail" for e in records),
            **{c: sum(e["counts"][c] for e in records)
               for c in ("error", "could-not-check", "warning", "info")},
        },
        "records": records,
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))
    return report


def print_summary(report: dict) -> None:
    s = report["summary"]
    print(f"\nRecords: {s['records']}   passed: {s['passed']}   failed: {s['failed']}")
    print(f"Issues:  {s['error']} errors, {s['could-not-check']} could-not-check, "
          f"{s['warning']} warnings, {s['info']} info")
    if report["package_substitutions"]:
        print(f"NOTE: {len(report['package_substitutions'])} package substitutions in effect "
              "(offline bootstrap). Do not publish these numbers.")
    print()
    for e in report["records"]:
        print(f"  [{e['status'].upper():4}] {e['record_id']}")
        for i in e["issues"]:
            if i["category"] == "error":
                where = i["source"] or i["expression"]
                print(f"         {where:<34} {i['message_id'].split('#')[-1]}: {i['message'][:90]}")
