"""report.json -> one self-contained dashboard page.

The dashboard only displays what the pipeline produced. It computes counts and groupings for
display, never verdicts: pass/fail, categories and source locations all come from the report.

What gets embedded:
  - every record, as a compact row (status, counts, issue types, links) so filters and
    totals cover the whole run, even at 17,000 records;
  - full detail (the raw HL7 message, the FHIR resource, every issue) for every record
    that failed or has a gateway-raised warning, plus a sample of passing patients and
    their lab reports. Small runs (the fixtures) embed everything.

Usage:  python -m gateway dashboard out/report.json --html out/dashboard.html
"""

from __future__ import annotations

import json
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent / "template.html"
EMBED_ALL_BELOW = 400          # records; smaller runs embed full detail for everything
SAMPLE_PATIENTS = 60           # passing patients with full detail in large runs
SAMPLE_LABS = 240              # their lab reports with full detail, at most
OBSERVATIONS_PER_REPORT = 8     # validated Observations embedded per lab report
KIND_CODES = {"patient": "P", "lab-report": "L", "unsupported": "U", "unparseable": "X"}

# Plain-language names and a one-line explanation per issue type. Explanations describe
# what the rule checks and its usual cause; they are hints, labelled as such in the page.
LABELS: dict[str, tuple[str, str]] = {
    "inv-ihi-value-0": ("IHI is not 16 digits",
                        "AU Base requires an IHI to be exactly 16 digits. Usually a truncated or padded value."),
    "inv-ihi-value-1": ("IHI prefix is not 800360",
                        "Every IHI starts 800360. Other 8003 prefixes are provider (HPI-I) or organisation (HPI-O) numbers."),
    "inv-ihi-value-2": ("IHI check digit fails",
                        "The last digit is a Luhn check digit. A mismatch usually means a mistyped or corrupted number."),
    "Validation_VAL_Profile_Minimum": ("Required detail missing",
                                       "AU Core requires this detail, but the record does not have it: the source field "
                                       "was empty, or held a value the gateway could not translate."),
    "au-core-pat-01": ("No usable patient identifier",
                       "AU Core needs at least one identifier, or a reason it is absent."),
    "Terminology_TX_ValueSet_NotFound": ("Australian value set not loaded",
                                         "The value set is published through NCTS, which needs registration. Not a failure: the code could not be checked."),
    "Error_validating_code_running_without_terminology_services": (
        "Code not checked offline",
        "Checking this code (for example a UCUM unit) needs a terminology server, and this run has none. Not a failure."),
    "TYPE_SPECIFIC_CHECKS_DT_QTY_UCUM_ANNOTATIONS": (
        "Unit relies on a UCUM annotation",
        "Units like mL/min/{1.73_m2} are valid, but the part in braces is ignored when units are compared. Kept as the lab sent it."),
    "mapping.unit-not-coded": ("Unit has no coding system",
                               "The unit is kept as text because the sender's unit system is not mapped, so it cannot be computed with."),
    "UNKNOWN_CODESYSTEM": ("Code system not loaded",
                           "The validator has no copy of this code system, so the code could not be checked."),
    "Validation_VAL_Profile_MultipleMatches": ("Fits more than one identifier profile",
                                               "AU Patient allows a generic identifier alongside the Australian ones, so the validator notes both match. Also appears on HL7's own examples."),
    "Details_for__matching_against_Profile_": ("Identifier outside AU Core's named slices",
                                               "A local record number is allowed but is not one of AU Core's named identifier types."),
    "ingest.bad-date": ("Invalid date",
                        "The date is impossible or not written the HL7 way (YYYY[MM[DD]]), so it was not converted. "
                        "The gateway never guesses which part is the day."),
    "ingest.bad-loinc-code": ("LOINC code fails its check digit",
                              "Every LOINC code ends in a check digit (LOINC Users' Guide, Appendix C). This one does not "
                              "match, so the code is mistyped or corrupted and the result was not converted."),
    "ingest.bad-datetime": ("Impossible result time", "The date or time does not exist, so it was not converted."),
    "ingest.datetime-no-timezone": ("Time without a timezone",
                                    "FHIR needs a timezone on any time. The result was kept as a date rather than guessing one."),
    "ingest.bad-numeric": ("Numeric result is not a number",
                           "The result is typed NM (numeric) but the value cannot be read as a number, for example a decimal comma."),
    "ingest.missing-code": ("Result has no test code", "OBX-3 is empty, so nobody can tell which test this is."),
    "ingest.no-results": ("Lab report has no results", "The message has no OBX segments."),
    "ingest.no-pid": ("Message names no patient", "The message has no PID segment."),
    "ingest.parse-error": ("File could not be read as HL7 v2", "The file is not well-formed HL7 v2."),
    "ingest.unmapped-identifier": ("Identifier type with no rule",
                                   "The sender's rules file does not say how to map this identifier type, so it was skipped."),
    "ingest.unsupported-message": ("Message type not handled", "This message type is outside the gateway's scope and was skipped."),
    "mapping.unmapped-code": ("Code with no mapping",
                              "The sender used a code the rules file does not translate. The gateway refuses to guess."),
    "link.orphan-result": ("Results for an unregistered patient",
                           "No registered patient has any of this report's identifiers, so the results cannot be filed."),
    "link.demographic-mismatch": ("Name or birth date differs",
                                  "The identifier matches a registered patient but the details differ. A person should check it is the right patient."),
}


def short_id(message_id: str) -> str:
    return message_id.rsplit("#", 1)[-1]


def label_for(message_id: str) -> tuple[str, str]:
    return LABELS.get(short_id(message_id), (short_id(message_id).replace("_", " "), ""))


def _source_lines(path: Path, cache: dict[Path, list[str]]) -> list[str]:
    """File lines numbered exactly as the parser numbers them."""
    if path not in cache:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        cache[path] = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return cache[path]


def load_resource(path: Path):
    """A resource file for embedding. Decimals become {"$decimal": "2.80"}: a browser's JSON
    parser would turn 2.80 into 2.8, and the page must show the digits as written."""
    return json.loads(path.read_text(), parse_float=lambda text: {"$decimal": text})


def select_detail(records: list[dict], always: set[str] = frozenset()) -> set[str]:
    """Record ids that get full detail embedded (always: e.g. every record with an injected fault)."""
    if len(records) < EMBED_ALL_BELOW:
        return {r["record_id"] for r in records}
    chosen = set(always) | {r["record_id"] for r in records
              if r["status"] != "pass"
              or any(i["stage"] in ("ingest", "mapping", "link") for i in r["issues"])}
    patients = [r for r in records if r["kind"] == "patient" and r["status"] == "pass"][:SAMPLE_PATIENTS]
    sampled = {r["record_id"] for r in patients}
    chosen |= sampled
    labs = [r for r in records if r["kind"] == "lab-report" and r.get("linked_patient") in sampled]
    chosen |= {r["record_id"] for r in labs[:SAMPLE_LABS]}
    return chosen


def build_data(report: dict, root: Path, resources: Path) -> dict:
    records = report["records"]
    index = {r["record_id"]: n for n, r in enumerate(records)}
    files = sorted({r["source_path"] for r in records})
    file_index = {f: n for n, f in enumerate(files)}

    # Issue types across the whole run.
    issues: dict[str, dict] = {}
    rank = {"error": 0, "could-not-check": 1, "warning": 2, "info": 3}
    for r in records:
        for i in r["issues"]:
            agg = issues.setdefault(i["message_id"], {
                "id": i["message_id"], "category": i["category"], "stage": i["stage"],
                "count": 0, "records": set(), "example": i["message"][:400]})
            agg["count"] += 1
            agg["records"].add(r["record_id"])
            if rank[i["category"]] < rank[agg["category"]]:
                agg["category"] = i["category"]
    issue_list = sorted(issues.values(), key=lambda a: (rank[a["category"]], -len(a["records"]), a["id"]))
    issue_index = {a["id"]: n for n, a in enumerate(issue_list)}

    rows = []
    for r in records:
        c = r["counts"]
        linked = r.get("linked_patient")
        rows.append([
            r["record_id"], KIND_CODES.get(r["kind"], "U"), 1 if r["status"] == "pass" else 0,
            c["error"], c["could-not-check"], c["warning"], c["info"],
            file_index[r["source_path"]],
            index.get(linked, -1) if linked else (-2 if r["kind"] == "lab-report" else -1),
            r.get("result_count", 0),
            sorted({issue_index[i["message_id"]] for i in r["issues"]}),
            r.get("observations", 0), len(r.get("resources", [])),
        ])

    fault_report = report.get("faults")
    detail_ids = select_detail(records, {f["record_id"] for f in fault_report["faults"]} if fault_report else set())
    cache: dict[Path, list[str]] = {}
    detail = {}
    for r in records:
        if r["record_id"] not in detail_ids:
            continue
        hl7 = []
        if r.get("lines"):
            first, last = r["lines"]
            lines = _source_lines(root / r["source_path"], cache)
            hl7 = [[n, lines[n - 1]] for n in range(first, last + 1) if n - 1 < len(lines) and lines[n - 1]]
        fhir = None
        if r.get("resource") and (resources / r["resource"]).exists():
            fhir = load_resource(resources / r["resource"])
        # Lab reports: the validated Observations, those with issues first, capped per report.
        flagged = {i.get("resource") for i in r["issues"] if i["stage"] == "validator"}
        obs_files = sorted(r.get("resources", []), key=lambda f: (f not in flagged, f))
        observations = [{"file": f, "json": load_resource(resources / f)}
                        for f in obs_files[:OBSERVATIONS_PER_REPORT] if (resources / f).exists()]
        detail[r["record_id"]] = {
            "hl7": hl7, "fhir": fhir, "linked_by": r.get("linked_by"),
            "observations": observations,
            "issues": [{"stage": i["stage"], "category": i["category"], "id": i["message_id"],
                        "expression": i.get("expression", ""), "message": i["message"],
                        "at": i.get("source_locator", ""), "line": _line_of(i.get("source", "")),
                        "resource": i.get("resource") or ""}
                       for i in r["issues"]],
        }

    return {
        "meta": {k: report.get(k) for k in ("generated", "validator", "versions", "terminology_server",
                                            "source_rules", "timing_seconds")}
                | {"substitutions": len(report.get("package_substitutions", [])),
                   "detail_records": len(detail), "total_records": len(records)},
        "summary": report["summary"],
        "files": files,
        "issues": [{"id": a["id"], "short": short_id(a["id"]), "label": label_for(a["id"])[0],
                    "hint": label_for(a["id"])[1], "category": a["category"], "stage": a["stage"],
                    "count": a["count"], "records": len(a["records"]), "example": a["example"]}
                   for a in issue_list],
        "rows": rows,
        "detail": detail,
        "faults": fault_data(fault_report, index) if fault_report else None,
    }


def fault_data(f: dict, row_of: dict[str, int]) -> dict:
    """Fault detection for the page: per type, its rows; per faulted row, what was changed."""
    names = [t["name"] for t in f["types"]]
    by_row = {}
    rows_of = {n: [] for n in names}
    for o in f["faults"]:
        row = row_of.get(o["record_id"])
        if row is None:
            continue
        rows_of[o["fault"]].append(row)
        by_row[row] = {"type": names.index(o["fault"]), "at": o["at"], "before": o["before"],
                       "after": o["after"], "detected": o["detected"], "caught_by": o.get("caught_by", []),
                       "confirmed_by": o.get("confirmed_by", []), "collateral": o.get("collateral", [])}
    return {
        "injected": f["injected"], "detected": f["detected"], "ledger": f["ledger"],
        "clean": f["clean_records"], "faulted": f["faulted_records"],
        "stages": f.get("stages", []), "collateral": f.get("collateral_findings", 0),
        "types": [{"name": t["name"], "what": t["what"], "applies_to": t["applies_to"],
                   "expect": [{"category": e["category"], "id": e["id"], "short": short_id(e["id"]),
                               "label": label_for(e["id"])[0]} for e in t["expect"]],
                   "injected": t["injected"], "detected": t["detected"], "rows": sorted(rows_of[t["name"]]),
                   "caught_by": t.get("caught_by", {}), "confirmed_by": t.get("confirmed_by", {}),
                   "collateral": t.get("collateral", 0)}
                  for t in f["types"]],
        "by_row": by_row,
    }


def _line_of(source: str) -> int | None:
    """'file.hl7:3 PID-7' -> 3"""
    try:
        return int(source.split(" ", 1)[0].rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None


def render(data: dict, fragment: bool = False) -> str:
    """The page. Data is embedded as JSON; '</' is escaped so nothing in the data (HL7 text,
    FHIR narrative) can close the script element early."""
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    payload = payload.replace("</", "<\\/").replace("<!--", "<\\!--")
    page = TEMPLATE.read_text().replace("__GATEWAY_DATA__", payload)
    if fragment:
        return page
    head, body = page.split("<!--head-end-->", 1)
    return ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">\n"
            + head + "</head>\n<body>\n" + body + "\n</body>\n</html>\n")


def build(report_path: Path, html_path: Path, fragment: bool = False) -> dict:
    report = json.loads(report_path.read_text())
    data = build_data(report, Path.cwd(), report_path.parent / "resources")
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(render(data, fragment), encoding="utf-8")
    return data
