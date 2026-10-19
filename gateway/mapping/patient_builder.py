"""Canonical PatientRecord -> AU Core Patient (FHIR R4 JSON), plus a trace table.

The builder decides how data is represented, never whether it is valid: that is the
validator's job. It does not drop or "fix" bad values. A broken IHI is emitted as received,
so the validator can report it and the trace can point at the source field.

The trace maps FHIR paths ("Patient.identifier[0]") to the SourceRef they came from.
Paths are recorded even for empty source fields, so a "missing birthDate" error still
points at PID-7.
"""

from __future__ import annotations

import html
import re

from gateway.identifiers.au import IHI_SYSTEM, MEDICARE_SYSTEM, V2_0203
from gateway.model.canonical import IngestIssue, PatientRecord, SourceRef, Traced

AU_CORE_PATIENT = "http://hl7.org.au/fhir/core/StructureDefinition/au-core-patient"
FHIR_ID = re.compile(r"[^A-Za-z0-9\-.]")

IDENTIFIER_TYPES = {
    "ihi": {"system": IHI_SYSTEM, "code": "NI", "text": "IHI"},
    "medicare": {"system": MEDICARE_SYSTEM, "code": "MC", "text": "Medicare Number"},
    "mrn": {"system": None, "code": "MR", "text": "Medical Record Number"},
}

Trace = dict[str, SourceRef]


def fhir_id(record_id: str) -> str:
    return FHIR_ID.sub("-", record_id)[:64]


def _whole(ref: SourceRef) -> SourceRef:
    """PID-5[1].2 -> PID-5[1]: the repetition a component belongs to."""
    return SourceRef(ref.file, ref.line, ref.locator.rsplit(".", 1)[0])


def build_patient(rec: PatientRecord, rules: dict) -> tuple[dict, Trace, list[IngestIssue]]:
    r = rules["patient"]
    trace: Trace = {}
    issues: list[IngestIssue] = []
    patient: dict = {
        "resourceType": "Patient",
        "id": fhir_id(rec.record_id),
        "meta": {"profile": [AU_CORE_PATIENT]},
    }
    pid_ref = SourceRef(rec.source_file,
                        rec.identifiers_source.line if rec.identifiers_source else 0, "PID")
    trace["Patient"] = pid_ref

    # identifier
    if rec.identifiers_source:
        trace["Patient.identifier"] = rec.identifiers_source
    identifiers = []
    mrn_rule = next((ru for ru in r["identifiers"]["rules"] if ru["kind"] == "mrn"), {})
    for ident in rec.identifiers:
        spec = IDENTIFIER_TYPES.get(ident.kind)
        if spec is None:
            continue
        entry = {
            "type": {"coding": [{"system": V2_0203, "code": spec["code"]}], "text": spec["text"]},
            "system": spec["system"] or mrn_rule.get("system"),
            "value": ident.value.value,
        }
        if ident.kind == "mrn" and mrn_rule.get("assigner_display"):
            entry["assigner"] = {"display": mrn_rule["assigner_display"]}
        trace[f"Patient.identifier[{len(identifiers)}]"] = ident.value.source
        identifiers.append(entry)
    if identifiers:
        patient["identifier"] = identifiers

    # name
    if rec.names_source:
        trace["Patient.name"] = rec.names_source
    names = []
    for name in rec.names:
        k = len(names)
        entry: dict = {"use": name.use}
        trace[f"Patient.name[{k}]"] = _whole(name.family.source)
        if name.family.value:
            entry["family"] = name.family.value
        trace[f"Patient.name[{k}].family"] = name.family.source
        if name.given:
            entry["given"] = [g.value for g in name.given]
            for j, g in enumerate(name.given):
                trace[f"Patient.name[{k}].given[{j}]"] = g.source
        names.append(entry)
    if names:
        patient["name"] = names

    # gender
    if rec.sex:
        trace["Patient.gender"] = rec.sex.source
        raw = rec.sex.value
        if raw:
            mapped = r["sex"]["map"].get(raw)
            if mapped:
                patient["gender"] = mapped
            else:
                issues.append(IngestIssue("error", "mapping.unmapped-code",
                                          f"sex code {raw!r} has no mapping in the rules file",
                                          rec.sex.source))

    # birthDate
    if rec.birth_date:
        trace["Patient.birthDate"] = rec.birth_date.source
        if rec.birth_date.value:
            patient["birthDate"] = rec.birth_date.value  # canonical dates are already ISO

    # address
    if rec.addresses_source:
        trace["Patient.address"] = rec.addresses_source
    addresses = []
    for addr in rec.addresses:
        k = len(addresses)
        entry = {"use": addr.use} if addr.use else {}
        trace[f"Patient.address[{k}]"] = _whole(addr.city.source)
        if addr.lines:
            entry["line"] = [ln.value for ln in addr.lines]
            for j, ln in enumerate(addr.lines):
                trace[f"Patient.address[{k}].line[{j}]"] = ln.source
        for fhir_name, traced in (("city", addr.city), ("state", addr.state),
                                  ("postalCode", addr.postcode)):
            trace[f"Patient.address[{k}].{fhir_name}"] = traced.source
            if traced.value:
                entry[fhir_name] = traced.value
        country = r["address"]["country"].get(addr.country.value.upper(), addr.country.value)
        trace[f"Patient.address[{k}].country"] = addr.country.source
        if country:
            entry["country"] = country
        addresses.append(entry)
    if addresses:
        patient["address"] = addresses

    patient["text"] = narrative(patient)
    # Keep resource keys in conventional FHIR order (text after meta) for readable output.
    order = ["resourceType", "id", "meta", "text"]
    patient = {k: patient[k] for k in order + [k for k in patient if k not in order]}
    return patient, trace, issues


def narrative(patient: dict) -> dict:
    """Minimal generated narrative (dom-6: a resource should have narrative). It summarises
    only what is already in the structured data, so it can never disagree with it."""
    name = (patient.get("name") or [{}])[0]
    who = " ".join(name.get("given", []) + [name.get("family", "")]).strip() or "Unnamed patient"
    facts = [f"Born {patient['birthDate']}" if "birthDate" in patient else "Birth date not recorded",
             f"gender {patient['gender']}" if "gender" in patient else "gender not recorded"]
    ihi = next((i["value"] for i in patient.get("identifier", []) if i["system"] == IHI_SYSTEM), None)
    if ihi:
        facts.append(f"IHI {ihi}")
    div = (f'<div xmlns="http://www.w3.org/1999/xhtml"><p><b>{html.escape(who, quote=False)}</b></p>'
           f"<p>{html.escape(', '.join(facts), quote=False)}</p></div>")
    return {"status": "generated", "div": div}


# Locating issues moved to gateway/mapping/trace.py (shared by every resource type);
# re-exported here so existing imports keep working.
from gateway.mapping.trace import ROOT_INVARIANT_PATHS, locate  # noqa: E402,F401
