"""What the Message Inspector page calls, inside the visitor's browser (Pyodide).

It runs the real pipeline, unchanged, on the text the visitor pasted, with one difference:
the official validator is not run, because it is a Java program and cannot run in a browser.
So the result shows the gateway's own checks (format, translation rules, patient matching)
and the FHIR it built, never a conformance verdict. The page says so.

Nothing here decides anything. For display it adds:
  - the message's lines, numbered exactly as the parser numbers them;
  - each built resource, with decimals kept as written ({"$decimal": "2.80"});
  - for every JSON path in each resource, the HL7 field it came from, taken from the
    builder's own trace (the longest traced path that contains it). Paths with no trace
    are what the gateway adds itself, and are shown as such.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from gateway import pipeline
from gateway.dashboard.build import label_for
from gateway.ingest.hl7v2 import text_lines
from gateway.model.canonical import SourceRef

RULES = "demo-lab"
WORK = Path("/tmp/inspector")


def _load(text: str):
    return json.loads(text, parse_float=lambda t: {"$decimal": t})


def json_paths(resource: dict) -> list[str]:
    """Every path the page's JSON view shows, in the same spelling: Patient.name[0].family.
    resourceType sits on the root path, as in the dashboard's renderJSON."""
    out: list[str] = []

    def walk(value, path: str) -> None:
        out.append(path)
        if isinstance(value, list):
            for i, item in enumerate(value):
                walk(item, f"{path}[{i}]")
        elif isinstance(value, dict) and set(value) != {"$decimal"}:
            for key, item in value.items():
                walk(item, path if key == "resourceType" else f"{path}.{key}")

    walk(resource, resource.get("resourceType", "Resource"))
    return list(dict.fromkeys(out))


_CHOICE = re.compile(r"^[A-Z]")


def _contains(traced: str, path: str) -> bool:
    if path == traced:
        return True
    if not path.startswith(traced):
        return False
    rest = path[len(traced):]
    # "Observation.effective" is the trace for "Observation.effectiveDateTime" (a choice type).
    return rest[0] in ".[" or bool(_CHOICE.match(rest))


def sources(resource: dict, trace: dict[str, SourceRef]) -> dict[str, dict]:
    """JSON path -> {"line", "at"} for every path covered by the trace. The trace's entry for
    the whole resource is left out: it would credit the HL7 segment with what the gateway
    adds itself (the id, the profile, the narrative), which has no single HL7 source."""
    out = {}
    root = resource.get("resourceType", "Resource")
    traced_paths = [t for t in trace if t != root]
    for path in json_paths(resource):
        best = max((t for t in traced_paths if _contains(t, path)), key=len, default=None)
        if best is not None:
            ref = trace[best]
            out[path] = {"line": ref.line, "at": ref.locator, "traced": best}
    return out


def inspect(text: str, name: str = "message.hl7", context: list[tuple[str, str]] = (),
            work: Path = WORK) -> dict:
    """Run the gateway on one file's text. name is the file name the record ids use.
    context: other files (name, text) run alongside it but not shown, such as the patient
    registrations a lab report needs in order to be filed; CI runs the examples the same way."""
    in_dir, out_dir = work / "in", work / "out"
    in_dir.mkdir(parents=True, exist_ok=True)
    for old in in_dir.glob("*"):
        old.unlink()
    main = in_dir / Path(name).name
    for other, other_text in context:
        if Path(other).name != main.name:
            (in_dir / Path(other).name).write_text(other_text, encoding="utf-8", newline="")
    main.write_text(text, encoding="utf-8", newline="")

    traces: dict[str, dict[str, SourceRef]] = {}
    report = pipeline.run(sorted(in_dir.glob("*")), RULES, out_dir, validate=False, traces_out=traces)

    def resource(file: str) -> dict:
        res = _load((out_dir / "resources" / file).read_text())
        return {"file": file, "json": res, "sources": sources(res, traces.get(file, {}))}

    def issue(i: dict) -> dict:
        label, hint = label_for(i["message_id"])
        return i | {"label": label, "hint": hint}

    records, on_file = [], []
    for entry in report["records"]:
        if entry["source_file"] != main.name:
            if entry["kind"] == "patient":
                on_file.append(entry["record_id"])
            continue
        rec = {k: entry.get(k) for k in ("kind", "record_id", "lines", "status",
                                          "linked_patient", "linked_by", "result_count", "observations")}
        rec["issues"] = [issue(i) for i in entry["issues"]]
        files = [entry["resource"]] if entry.get("resource") else entry.get("resources") or []
        rec["resources"] = [resource(f) for f in files]
        records.append(rec)

    return {"file": main.name, "lines": text_lines(text), "rules": RULES,
            "validator": report["validator"], "registrations_on_file": on_file, "records": records}


def inspect_json(text: str, name: str = "message.hl7", context_json: str = "[]") -> str:
    """inspect() as JSON text, for the page. context_json: [[name, text], ...]."""
    context = [tuple(c) for c in json.loads(context_json)]
    return json.dumps(inspect(text, name, context), ensure_ascii=False)
