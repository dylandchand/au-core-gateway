"""Builds the Message Inspector: one static page plus the gateway's Python code, zipped.

The page loads Pyodide (Python compiled for the browser) from its official CDN and runs this
repository's own gateway code on whatever the visitor pastes. Nothing is sent anywhere: the
only downloads are the page, this zip (the gateway plus PyYAML, with its licence), Pyodide
itself and the fonts.

The built-in examples (fixtures/) come with what CI recorded for them: the official
validator's findings, and whether each example behaved as fixtures/expectations.yaml says
(gateway/examples.py, the integration tests' rule). Those come from a report.json; a report
made with stand-in packages is refused unless allow_substituted (local previews only).

Usage:  python -m gateway inspector --report out/report.json --out out/inspector
"""

from __future__ import annotations

import json
import tomllib
import zipfile
from importlib import metadata
from pathlib import Path

from gateway import examples, provenance, site
from gateway.dashboard.build import EXAMPLE_NAMES, hl7_names, label_for
from gateway.ingest.hl7v2 import parse_text

ROOT = Path(__file__).resolve().parents[2]
PAGE = Path(__file__).resolve().parent / "page.html"
REGISTRATIONS = "clean-patients.hl7"     # the registrations lab examples are filed against
ZIP_DATE = (1980, 1, 1, 0, 0, 0)         # fixed, so the same code gives the same zip
SECTIONS = [("message", "Choose a message", None), ("results", "Result", None),
            ("problems", "Problems", None), ("data", "The data", None), ("about", "About this page", None)]


def _pyyaml() -> tuple[str, list[tuple[str, bytes]]]:
    """The installed PyYAML (pinned in pyproject.toml) as pure Python, plus its MIT licence,
    which must travel with it. Its optional C speed-up is left out: browsers cannot load it,
    and PyYAML falls back to pure Python on its own."""
    dist = metadata.distribution("PyYAML")
    files = []
    for f in dist.files:
        name = str(f)
        if name.startswith("yaml/") and name.endswith(".py"):
            files.append((name, Path(dist.locate_file(f)).read_bytes()))
        elif "LICENSE" in name.upper():
            files.append(("yaml/LICENSE", Path(dist.locate_file(f)).read_bytes()))
    return dist.version, sorted(files)


def bundle(path: Path) -> dict:
    """Zip the gateway package (Python files, rules, names) and versions.toml, laid out as in
    the repository so the code finds its files the same way, plus PyYAML."""
    own = sorted([p for p in (ROOT / "gateway").rglob("*") if p.is_file() and "__pycache__" not in p.parts
                  and p.suffix in (".py", ".yaml", ".json")] + [ROOT / "versions.toml"])
    entries = [(p.relative_to(ROOT).as_posix(), p.read_bytes()) for p in own]
    yaml_version, yaml_files = _pyyaml()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries + yaml_files:
            info = zipfile.ZipInfo(name, ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    return {"files": len(entries), "pyyaml": yaml_version}


def _is_lab_file(text: str) -> bool:
    try:
        return any(m.message_type.startswith("ORU") for m in parse_text(text))
    except Exception:
        return False


def example_list(expect: dict) -> list[dict]:
    out = []
    for group in examples.GROUPS:
        for f in sorted((examples.FIXTURES / group).glob("*.hl7")):
            text = f.read_text(encoding="utf-8")
            spec = (expect.get(group) or {}).get(f.name) or {}
            out.append({
                "file": f.name, "group": group, "text": text,
                "name": EXAMPLE_NAMES.get(f.name, f.stem.replace("-", " ")),
                "why": " ".join(spec.get("why", "").split()),
                "expect": [{"category": c, "id": e["id"], "at": e["at"],
                            "label": label_for(e["id"])[0]}
                           for c, key in (("error", "errors"), ("warning", "warnings"))
                           for e in spec.get(key) or []],
                "context": [REGISTRATIONS] if f.name != REGISTRATIONS and _is_lab_file(text) else [],
            })
    return out


def recorded(report: dict) -> dict:
    """What CI recorded for the built-in examples: validator findings per record, and the
    examples check (gateway/examples.py) per file."""
    fixtures_paths = {r["source_path"] for r in report["records"]
                      if examples.group_of(ROOT / r["source_path"], examples.FIXTURES)}
    paths = {p: (examples.group_of(ROOT / p, examples.FIXTURES), Path(p).name) for p in fixtures_paths}
    scored = examples.score(report["records"], examples.load(), paths) or {}
    per_file = {i["file"]: i["as_expected"] for i in (scored.get("broken") or {}).get("items", [])}
    clean = scored.get("clean") or {}
    records = {}
    for r in report["records"]:
        if r["source_path"] not in paths:
            continue
        found = [i | {"label": label_for(i["message_id"])[0], "hint": label_for(i["message_id"])[1]}
                 for i in r["issues"] if i["stage"] == "validator"]
        records[r["record_id"]] = {"status": r["status"], "issues": found, "file": Path(r["source_path"]).name}
    return {
        "generated": report.get("generated"), "validator": report.get("validator"),
        "terminology_server": report.get("terminology_server"),
        "substituted": bool(report.get("package_substitutions")),
        "records": records,
        "as_expected": per_file | ({REGISTRATIONS: clean.get("passed") == clean.get("records")}
                                   if clean.get("files") else {}),
    }


def build(out_dir: Path, report_path: Path | None = None, allow_substituted: bool = False) -> Path:
    cfg = tomllib.loads((ROOT / "versions.toml").read_text())
    rec = None
    if report_path is not None:
        report = json.loads(Path(report_path).read_text())
        if report.get("package_substitutions") and not allow_substituted:
            raise SystemExit("refusing a report made with stand-in packages (package_substitutions); "
                             "its validator findings must not be published")
        rec = recorded(report)
    out_dir.mkdir(parents=True, exist_ok=True)
    zipped = bundle(out_dir / "gateway.zip")
    data = {
        "pyodide": cfg["inspector"]["pyodide"],
        "versions": cfg["packages"], "fhir": cfg["fhir"]["version"],
        "hl7": hl7_names(), "examples": example_list(examples.load()), "recorded": rec,
        "bundle": zipped,
    }
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/").replace("<!--", "<\\!--")
    page = site.fill(PAGE.read_text(), "inspector", SECTIONS, site.TECH_SWITCH,
                     "This page runs the gateway's own code on a message, in your browser.")
    page = page.replace("__PYODIDE_VERSION__", cfg["inspector"]["pyodide"]).replace("__INSPECTOR_DATA__", payload)
    # The examples it offers are the project's fixtures: say where they come from.
    sources = provenance.describe([examples.FIXTURES / group for group in examples.GROUPS])
    page = page.replace("__PROVENANCE__", provenance.html(sources))
    (out_dir / "index.html").write_text(page)
    return out_dir / "index.html"
