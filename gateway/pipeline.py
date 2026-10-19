"""The end-to-end run: HL7 v2 files -> canonical -> AU Core FHIR -> validator -> report.

Two passes over the input, because an Observation must reference its patient:

  Pass 1  registrations (ADT)  -> PatientRecord -> AU Core Patient + trace
  Pass 2  lab results (ORU^R01) -> LabReport, linked to a registered patient (IHI, then MRN,
          then Medicare); each result that passed ingest checks -> AU Core pathology
          Observation + trace. Results stream through, so memory stays flat at any volume.
  Then    one validator run over every Patient and the selected Observations
  Report  every issue joined back to its HL7 source field through the trace

Which Observations are validated (see ObservationSelection): all of them in small runs.
In large runs, every distinct result shape (test code, value type, unit, status) plus a
fixed-seed random sample, so the validator's hour-long job takes about a minute while every
kind of result the feed contains is still checked. The report states the coverage.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from gateway import faults
from gateway.ingest.hl7v2 import HL7ParseError, parse_file
from gateway.ingest.lab_adapter import extract_lab_report
from gateway.ingest.patient_adapter import extract_patient, load_rules
from gateway.mapping.fhir_json import dumps
from gateway.mapping.observation_builder import build_observation
from gateway.mapping.patient_builder import build_patient
from gateway.mapping.trace import locate
from gateway.model.canonical import IngestIssue, LabReport, SourceRef
from gateway.validate import validator

PATIENT_EVENTS = {"ADT^A01", "ADT^A04", "ADT^A08", "ADT^A28", "ADT^A31"}
LAB_EVENTS = {"ORU^R01"}
LINK_ORDER = ("ihi", "mrn", "medicare")      # most to least trustworthy
CATEGORIES = ("error", "could-not-check", "warning", "info")
VALIDATE_ALL_UP_TO = 2000                    # lab reports; larger runs validate a selection
SAMPLE_PER_THOUSAND = 5                      # random share validated beyond one per shape


def _issue_dict(issue: IngestIssue, stage: str, resource: str | None = None) -> dict:
    return {"stage": stage, "severity": issue.severity,
            "category": "error" if issue.severity == "error" else "warning",
            "message_id": issue.code, "expression": "", "message": issue.message,
            "source": str(issue.source), "source_locator": issue.source.locator,
            "resource": resource}


def _new_entry(kind: str, record_id: str, file: str, control_id: str,
               path: Path | None = None, lines: tuple[int, int] | None = None) -> dict:
    return {"kind": kind, "record_id": record_id, "source_file": file,
            "source_path": _display_path(path) if path else file,
            "lines": list(lines) if lines else None,
            "message_control_id": control_id, "resource": None, "issues": []}


def _display_path(path: Path) -> str:
    """Path as shown in reports: relative to the working directory when possible, so a
    published report never carries a machine's absolute paths (home folders, runner dirs)."""
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return path.name


class ObservationSelection:
    """Decides which built Observations go to the validator.

    Conformance depends on a result's shape: which test, value type, unit and status. Two
    final mg/dL glucose results differ only in their numbers, which the profile does not
    constrain. So large runs validate the first result of every shape, plus a random share
    chosen by a hash of the Observation id (the same results every run, on every machine).
    """

    def __init__(self, validate_all: bool, per_thousand: int = SAMPLE_PER_THOUSAND):
        self.validate_all = validate_all
        self.per_thousand = per_thousand
        self.shapes: set[tuple] = set()
        self.shapes_validated: set[tuple] = set()
        self.built = self.selected = 0

    def choose(self, obs: dict) -> bool:
        q = obs.get("valueQuantity", {})
        shape = (tuple((c.get("system"), c.get("code")) for c in obs["code"].get("coding", [])),
                 "Quantity" if q else "string" if "valueString" in obs else "none",
                 q.get("system"), q.get("code"), obs.get("status"))
        self.built += 1
        self.shapes.add(shape)
        if self.validate_all or shape not in self.shapes_validated:
            chosen = True
        else:
            digest = hashlib.sha256(obs["id"].encode()).digest()
            chosen = int.from_bytes(digest[:4], "big") % 1000 < self.per_thousand
        if chosen:
            self.shapes_validated.add(shape)
            self.selected += 1
        return chosen

    def summary(self) -> dict:
        return {"built": self.built, "validated": self.selected,
                "shapes": len(self.shapes), "shapes_validated": len(self.shapes_validated),
                "mode": "all" if self.validate_all else
                        f"every shape + {self.per_thousand}/1000 sample"}


def _index(patients: list[tuple[dict, dict]]) -> dict[tuple[str, str], dict]:
    index: dict[tuple[str, str], dict] = {}
    for entry, patient in patients:
        for ident in patient.get("identifier", []):
            kind = {"NI": "ihi", "MR": "mrn", "MC": "medicare"}.get(ident["type"]["coding"][0]["code"])
            if kind and ident.get("value"):
                index.setdefault((kind, ident["value"]), {"entry": entry, "patient": patient})
    return index


def _link_one(entry: dict, report: LabReport, index: dict) -> dict | None:
    """Attach one lab report to a registered patient, or flag it. Returns the Patient."""
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
        return None
    patient = match["patient"]
    entry["linked_patient"] = match["entry"]["record_id"]
    entry["linked_by"] = used.kind
    registered_family = (patient.get("name") or [{}])[0].get("family", "")
    # Compare only what both sides recorded: a value missing from the registration is already
    # reported on the Patient, and is not a mismatch.
    if report.patient_family and report.patient_family.value and registered_family \
            and report.patient_family.value.casefold() != registered_family.casefold():
        entry["issues"].append(_issue_dict(IngestIssue(
            "warning", "link.demographic-mismatch",
            f"family name {report.patient_family.value!r} differs from registered "
            f"{registered_family!r} for the same {used.kind.upper()}",
            report.patient_family.source), "link"))
    if report.patient_birth_date and report.patient_birth_date.value and patient.get("birthDate") \
            and report.patient_birth_date.value != patient["birthDate"]:
        entry["issues"].append(_issue_dict(IngestIssue(
            "warning", "link.demographic-mismatch",
            f"birth date {report.patient_birth_date.value} differs from registered "
            f"{patient.get('birthDate')} for the same {used.kind.upper()}",
            report.patient_birth_date.source), "link"))
    return patient


def _link(patients: list[tuple[dict, dict]], labs: list[tuple[dict, LabReport]]) -> None:
    """Link a batch of lab reports (kept for callers and tests that link without building)."""
    index = _index(patients)
    for entry, report in labs:
        _link_one(entry, report, index)


def _messages(path: Path, entries: list[dict]):
    """Parsed messages of one file; an unreadable file becomes one failed record."""
    try:
        return parse_file(path)
    except HL7ParseError as exc:
        entry = _new_entry("unparseable", path.stem, path.name, "", path)
        entry["issues"].append(_issue_dict(IngestIssue(
            "error", "ingest.parse-error", str(exc), SourceRef(path.name, exc.line, "")), "ingest"))
        entries.append(entry)
        return None


def run(inputs: list[Path], source: str, out_dir: Path, validate_all_observations: bool | None = None,
        sample_per_thousand: int = SAMPLE_PER_THOUSAND, fault_ledger: Path | None = None) -> dict:
    """fault_ledger: faults.json from scripts/inject_faults.py; adds a "faults" section that
    measures detection against it (gateway/faults.py)."""
    rules = load_rules(source)
    resources_dir = out_dir / "resources"
    resources_dir.mkdir(parents=True, exist_ok=True)
    for old in resources_dir.glob("*.json"):
        old.unlink()

    files = sorted(p for i in inputs for p in ([i] if i.is_file() else i.rglob("*.hl7")))
    started = datetime.now(timezone.utc)
    entries: list[dict] = []
    traces: dict[str, dict[str, SourceRef]] = {}
    owner: dict[str, dict] = {}                  # resource file -> report entry it belongs to
    patients: list[tuple[dict, dict]] = []
    lab_files: list[Path] = []
    lab_count = 0

    # Pass 1: registrations.
    for path in files:
        messages = _messages(path, entries)
        if messages is None:
            continue
        for msg in messages:
            record_id = f"{path.stem}-{msg.control_id}"
            span = (msg.segments[0].line, msg.segments[-1].line)
            if msg.message_type in LAB_EVENTS:
                lab_count += 1
                if not lab_files or lab_files[-1] != path:
                    lab_files.append(path)
                continue
            if msg.message_type not in PATIENT_EVENTS:
                entry = _new_entry("unsupported", record_id, path.name, msg.control_id, path, span)
                entry["issues"].append(_issue_dict(IngestIssue(
                    "warning", "ingest.unsupported-message",
                    f"message type {msg.message_type!r} is not handled; skipped",
                    SourceRef(path.name, msg.msh.line, "MSH-9")), "ingest"))
                entries.append(entry)
                continue
            entry = _new_entry("patient", record_id, path.name, msg.control_id, path, span)
            rec, issues = extract_patient(msg, source, rules)
            entry["issues"] += [_issue_dict(i, "ingest") for i in issues]
            if rec is not None:
                patient, trace, map_issues = build_patient(rec, rules)
                entry["issues"] += [_issue_dict(i, "mapping") for i in map_issues]
                resource_file = f"{patient['id']}.json"
                (resources_dir / resource_file).write_text(dumps(patient))
                entry["resource"] = resource_file
                traces[resource_file] = trace
                owner[resource_file] = entry
                patients.append((entry, patient))
            entries.append(entry)

    # Pass 2: lab results, linked and converted as they stream past.
    index = _index(patients)
    if validate_all_observations is None:
        validate_all_observations = lab_count <= VALIDATE_ALL_UP_TO
    selection = ObservationSelection(validate_all_observations, sample_per_thousand)
    for path in lab_files:
        for msg in parse_file(path):
            if msg.message_type not in LAB_EVENTS:
                continue
            span = (msg.segments[0].line, msg.segments[-1].line)
            entry = _new_entry("lab-report", f"{path.stem}-{msg.control_id}", path.name,
                               msg.control_id, path, span)
            entries.append(entry)
            report, issues = extract_lab_report(msg, rules)
            entry["issues"] += [_issue_dict(i, "ingest") for i in issues]
            if report is None:
                continue
            entry["result_count"] = len(report.results)
            entry["observations"] = 0
            entry["resources"] = []
            patient = _link_one(entry, report, index)
            if patient is None:
                continue
            # Results with an ingest error are not converted: the error already fails the
            # report, and a resource built from a broken value would only restate it.
            broken = {i.source.locator.split("-", 1)[0] for i in issues if i.severity == "error"}
            for result in report.results:
                if result.segment.locator in broken:
                    continue
                obs, trace, map_issues = build_observation(report, result, patient["id"], rules)
                entry["observations"] += 1
                resource_file = f"{obs['id']}.json"
                entry["issues"] += [_issue_dict(i, "mapping", resource_file) for i in map_issues]
                if selection.choose(obs):
                    (resources_dir / resource_file).write_text(dumps(obs))
                    traces[resource_file] = trace
                    owner[resource_file] = entry
                    entry["resources"].append(resource_file)

    # One validator run over every Patient and the selected Observations.
    validated_at = datetime.now(timezone.utc)
    results = validator.validate_folder(resources_dir) if traces else {}
    for file, issues in results.items():
        entry = owner.get(file)
        if entry is None:
            continue
        for issue in issues:
            ref = locate(traces[file], issue.expression, issue.message, issue.message_id)
            entry["issues"].append({"stage": "validator", **issue.to_dict(),
                                    "source": str(ref) if ref else "",
                                    "source_locator": ref.locator if ref else "",
                                    "resource": file})
    finished = datetime.now(timezone.utc)

    for entry in entries:
        cats = [i["category"] for i in entry["issues"]]
        entry["status"] = "fail" if "error" in cats else "pass"
        entry["counts"] = {c: cats.count(c) for c in CATEGORIES}

    report = _report(entries, files, source, out_dir, selection, started, validated_at, finished)
    report = {"inputs": [_display_path(i) for i in inputs]} | report
    if fault_ledger is not None:
        report["faults"] = faults.score(report["records"], faults.load_ledger(fault_ledger))
    (out_dir / "report.json").write_text(dumps(report, indent=1))
    return report


def _report(entries, files, source, out_dir, selection, started, validated_at, finished) -> dict:
    def kind(k: str) -> list[dict]:
        return [e for e in entries if e["kind"] == k]
    labs = kind("lab-report")
    obs_failed = {i["resource"] for e in labs for i in e["issues"]
                  if i["stage"] == "validator" and i["category"] == "error"}
    obs = selection.summary() | {"failed_validation": len(obs_failed),
                                 "not_converted": sum(e.get("result_count", 0) - e.get("observations", 0)
                                                      for e in labs)}
    return {
        "generated": finished.isoformat(timespec="seconds"),
        "source_rules": source,
        "out_dir": _display_path(out_dir),
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
            "lab_reports": {"total": len(labs),
                            "linked": sum(bool(e.get("linked_patient")) for e in labs),
                            "orphans": sum(e.get("linked_patient") is None and "result_count" in e for e in labs),
                            "results": sum(e.get("result_count", 0) for e in labs),
                            "passed": sum(e["status"] == "pass" for e in labs)},
            "observations": obs,
            **{c: sum(e["counts"][c] for e in entries) for c in CATEGORIES},
        },
        "records": entries,
    }


def print_summary(report: dict, max_failures: int = 25) -> None:
    s = report["summary"]
    p, lab, obs = s["patients"], s["lab_reports"], s["observations"]
    print(f"\nFiles: {s['files']}   records: {s['records']}   passed: {s['passed']}   failed: {s['failed']}")
    print(f"Patients:     {p['passed']}/{p['total']} conform to AU Core")
    print(f"Lab reports:  {lab['linked']}/{lab['total']} linked to a registered patient "
          f"({lab['results']} results, {lab['orphans']} orphan{'' if lab['orphans'] == 1 else 's'})")
    print(f"Observations: {obs['built']} built, {obs['validated']} validated "
          f"({obs['shapes_validated']}/{obs['shapes']} result shapes; {obs['mode']}), "
          f"{obs['failed_validation']} failed, {obs['not_converted']} results not converted")
    print(f"Issues:       {s['error']} errors, {s['could-not-check']} could-not-check, "
          f"{s['warning']} warnings, {s['info']} info")
    t = report["timing_seconds"]
    print(f"Time:         {t['ingest_and_mapping']}s ingest + mapping, "
          f"{t['validation_and_linking']}s validation")
    if "faults" in report:
        print_fault_summary(report["faults"])
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


def print_fault_summary(f: dict) -> None:
    c, ft = f["clean_records"], f["faulted_records"]
    print(f"Faults:       {f['detected']}/{f['injected']} injected faults detected at the right field; "
          f"{c['false_alarms']} false alarms on {c['total']} untouched records")
    print(f"Collateral:   {f['collateral_findings']} findings at fields the injector did not change")
    print(f"Pass rate:    {c['passed']}/{c['total']} untouched records, {ft['passed']}/{ft['total']} faulted records")
    for t in f["types"]:
        caught = "+".join(st for st in f["stages"] if t["caught_by"][st]) or "nothing"
        confirmed = "+".join(st for st in f["stages"] if t["confirmed_by"][st])
        print(f"  fault {t['name']:<24} {t['detected']}/{t['injected']} detected, caught by {caught}"
              + (f", confirmed by {confirmed}" if confirmed else ""))
