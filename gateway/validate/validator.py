"""Run the official HL7 FHIR Validator over a folder of resources and parse its output.

The validator is the only judge of conformance in this project. This module never adds
rules of its own: it runs the validator with the pinned packages, reads the Bundle of
OperationOutcomes it writes, and sorts each issue into a category for reporting.

Categories:
  error            severity error/fatal: the resource does not conform
  could-not-check  the validator could not evaluate a code because the value set or code
                   system is unavailable (no terminology server, or NCTS content not loaded).
                   Neither a pass nor a fail; reported separately so it is never hidden.
  warning          other warnings
  info             information-level notes
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JAR = ROOT / "tools" / "validator_cli.jar"
SUBSTITUTIONS = Path.home() / ".fhir" / "packages" / "SUBSTITUTIONS.txt"

# Validator message ids meaning "could not evaluate this code", not "this code is wrong".
COULD_NOT_CHECK_IDS = {
    "Terminology_TX_ValueSet_NotFound",
    "Terminology_TX_ValueSet_NotFound_CS",
    "UNKNOWN_CODESYSTEM",
    "UNKNOWN_CODESYSTEM_VERSION",
    "Terminology_TX_System_NotKnown",
    "TERMINOLOGY_TX_SYSTEM_NO_CODE",
    "Terminology_PassThrough_TX_Message",
}

EXT = "http://hl7.org/fhir/StructureDefinition/"


@dataclass
class Issue:
    severity: str
    category: str
    message_id: str
    expression: str
    message: str
    line: int | None = None
    col: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _ext(element: dict, name: str):
    for ext in element.get("extension", []):
        if ext.get("url") == EXT + name:
            return next((v for k, v in ext.items() if k.startswith("value")), None)
    return None


def categorise(severity: str, message_id: str) -> str:
    if severity in ("error", "fatal"):
        return "error"
    if message_id in COULD_NOT_CHECK_IDS:
        return "could-not-check"
    return "warning" if severity == "warning" else "info"


def config() -> dict:
    return tomllib.loads((ROOT / "versions.toml").read_text())


def command(target: Path, output: Path) -> list[str]:
    cfg = config()
    xmx = os.environ.get("GATEWAY_VALIDATOR_XMX", "3g")
    return ["java", f"-Xmx{xmx}", "-XX:+UseStringDeduplication", "-jar", str(JAR), str(target),
            "-version", cfg["fhir"]["version"],
            "-ig", f"hl7.fhir.au.core#{cfg['packages']['hl7.fhir.au.core']}",
            "-tx", cfg["terminology"]["server"],
            "-output", str(output)]


def parse_output(data: dict) -> dict[str, list[Issue]]:
    """Validator output (one OperationOutcome, or a Bundle of them) -> issues per file name."""
    outcomes = ([e["resource"] for e in data.get("entry", [])]
                if data.get("resourceType") == "Bundle" else [data])
    results: dict[str, list[Issue]] = {}
    for oo in outcomes:
        file = Path(_ext(oo, "operationoutcome-file") or "unknown").name
        issues = []
        for i in oo.get("issue", []):
            message_id = _ext(i, "operationoutcome-message-id") or ""
            # The validator adds an informational "all OK" issue when there is nothing to say.
            if i.get("severity") == "information" and i.get("code") == "informational" \
                    and not i.get("expression") and "All OK" in i.get("details", {}).get("text", ""):
                continue
            issues.append(Issue(
                severity=i["severity"],
                category=categorise(i["severity"], message_id),
                message_id=message_id,
                expression=(i.get("expression") or i.get("location") or [""])[0],
                message=i.get("details", {}).get("text", ""),
                line=_ext(i, "operationoutcome-issue-line"),
                col=_ext(i, "operationoutcome-issue-col")))
        results[file] = issues
    return results


def validate_folder(folder: Path, timeout: int = 1800) -> dict[str, list[Issue]]:
    """Validate every .json resource in folder with one validator run."""
    if not JAR.exists():
        raise FileNotFoundError(f"{JAR} missing: run `make setup` first")
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "outcome.json"
        proc = subprocess.run(command(folder, output), capture_output=True, text=True, timeout=timeout)
        if not output.exists():
            tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-15:])
            raise RuntimeError(f"validator produced no output (exit {proc.returncode}):\n{tail}")
        return parse_output(json.loads(output.read_text()))


def substitutions() -> list[str]:
    """Package substitutions made by the offline bootstrap. Must be empty for publishable runs."""
    return SUBSTITUTIONS.read_text().splitlines() if SUBSTITUTIONS.exists() else []


def validator_version() -> str:
    path = ROOT / "tools" / "validator.version"
    return path.read_text().strip() if path.exists() else "unknown"
