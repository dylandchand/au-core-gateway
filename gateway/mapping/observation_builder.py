"""Canonical LabResult -> AU Core pathology result Observation (au-core-diagnosticresult-path).

Profile requirements, read from AU Core 2.0.0 (StructureDefinition-au-core-diagnosticresult-path):
  status 1..1 (required binding: observation-status)
  category 1..*, with the slice "lab": observation-category#laboratory
  code 1..1 (extensible binding to the SPIA pathology value set, published through NCTS)
  subject 1..1 -> AU Core Patient
  effective[x] 1..1, at least to the day (au-core-obs-02)
  value[x] or dataAbsentReason when there are no components (au-core-obs-01)

Like the Patient builder, this decides representation, never validity. Values are not repaired
or converted: numbers keep their digits, units stay as the lab sent them. Results that failed
ingest checks (a non-numeric NM value, an impossible time) are not converted at all; the ingest
error already fails the report, and a resource built from a broken value would only restate it.
"""

from __future__ import annotations

import html
import re

from gateway.mapping.fhir_json import fhir_decimal
from gateway.model.canonical import IngestIssue, LabReport, LabResult, SourceRef

AU_CORE_PATH_RESULT = "http://hl7.org.au/fhir/core/StructureDefinition/au-core-diagnosticresult-path"
OBS_CATEGORY = "http://terminology.hl7.org/CodeSystem/observation-category"
FHIR_ID = re.compile(r"[^A-Za-z0-9\-.]")

Trace = dict[str, SourceRef]


def observation_id(report: LabReport, result: LabResult) -> str:
    # The OBX occurrence (OBX(3) -> 3), unique in the message. OBX-1 set ids can repeat:
    # real feeds restart them under each OBR.
    occurrence = result.segment.locator.split("(", 1)[1].rstrip(")")
    return FHIR_ID.sub("-", f"{report.record_id}-{occurrence}")[:64]


def build_observation(report: LabReport, result: LabResult, patient_id: str,
                      rules: dict) -> tuple[dict, Trace, list[IngestIssue]]:
    fhir = rules["lab_results"]["fhir"]
    issues: list[IngestIssue] = []
    seg = result.segment
    trace: Trace = {"Observation": seg}
    obs: dict = {
        "resourceType": "Observation",
        "id": observation_id(report, result),
        "meta": {"profile": [AU_CORE_PATH_RESULT]},
    }

    status = fhir["status"].get(result.status.value)
    trace["Observation.status"] = result.status.source
    if status:
        obs["status"] = status
    else:
        issues.append(IngestIssue("error", "mapping.unmapped-code",
                                  f"result status {result.status.value!r} has no mapping in the rules file",
                                  result.status.source))

    obs["category"] = [{"coding": [{"system": OBS_CATEGORY, "code": "laboratory"}]}]
    trace["Observation.category"] = seg

    trace["Observation.code"] = result.code.source
    system = fhir["coding_systems"].get(result.code_system.value)
    code: dict = {}
    if system:
        coding = {"system": system, "code": result.code.value}
        if result.display.value:
            coding["display"] = result.display.value
        code["coding"] = [coding]
    else:
        issues.append(IngestIssue("error", "mapping.unmapped-code",
                                  f"coding system {result.code_system.value!r} has no mapping in the rules file",
                                  result.code_system.source))
    if result.display.value:
        code["text"] = result.display.value
    obs["code"] = code

    obs["subject"] = {"reference": f"Patient/{patient_id}"}
    trace["Observation.subject"] = report.identifiers_source or seg

    trace["Observation.effective"] = result.effective.source
    if result.effective.value:
        obs["effectiveDateTime"] = result.effective.value

    if fhir.get("performer_display"):
        obs["performer"] = [{"display": fhir["performer_display"]}]
        trace["Observation.performer"] = seg

    value_type = result.value_type.value
    if result.value.value:
        if value_type in rules["lab_results"]["numeric_types"]:
            quantity: dict = {"value": fhir_decimal(result.value.value)}
            trace["Observation.valueQuantity"] = result.value.source
            if result.units.value:
                quantity["unit"] = result.units.value
                unit_system = fhir["unit_systems"].get(result.units_system.value)
                if unit_system:
                    quantity["system"] = unit_system
                    quantity["code"] = result.units.value
                else:
                    issues.append(IngestIssue(
                        "warning", "mapping.unit-not-coded",
                        f"unit system {result.units_system.value or '(none)'!r} is not mapped; "
                        "the unit is kept as text only", result.units_system.source))
                for part in ("unit", "system", "code"):
                    trace[f"Observation.valueQuantity.{part}"] = result.units.source
            obs["valueQuantity"] = quantity
        else:
            obs["valueString"] = result.value.value
            trace["Observation.valueString"] = result.value.source
    trace["Observation.value"] = result.value.source

    obs["text"] = narrative(obs)
    order = ["resourceType", "id", "meta", "text"]
    return {k: obs[k] for k in order + [k for k in obs if k not in order]}, trace, issues


def narrative(obs: dict) -> dict:
    """Generated narrative (dom-6) restating only what is in the structured data."""
    what = obs["code"].get("text") or "Result"
    if "valueQuantity" in obs:
        q = obs["valueQuantity"]
        value = f"{q['value']} {q.get('unit', '')}".strip()
    else:
        value = obs.get("valueString", "no value")
    when = obs.get("effectiveDateTime", "time not recorded")
    status = obs.get("status", "status unknown")
    div = (f'<div xmlns="http://www.w3.org/1999/xhtml"><p><b>{html.escape(what, quote=False)}</b>: '
           f"{html.escape(str(value), quote=False)}</p><p>{html.escape(when, quote=False)}, "
           f"{html.escape(status, quote=False)}</p></div>")
    return {"status": "generated", "div": div}
