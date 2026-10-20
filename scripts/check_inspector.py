"""Proves the Message Inspector gives, inside Pyodide, exactly what the command line gives.

Builds the inspector, runs every built-in example through the bundle in Pyodide under Node
(scripts/inspector_pyodide.mjs), and compares records, issues and every FHIR resource with
the same examples run by the gateway on this machine (official validator skipped on both sides).

Usage:  python scripts/check_inspector.py <pyodide dir>
        (the pyodide npm package at the version pinned in versions.toml, e.g. node_modules/pyodide)
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gateway.inspector import bridge, build  # noqa: E402


def main(pyodide_dir: str) -> int:
    pinned = tomllib.loads((ROOT / "versions.toml").read_text())["inspector"]["pyodide"]
    found = json.loads((Path(pyodide_dir) / "package.json").read_text())["version"]
    if found != pinned:
        print(f"pyodide {found} found, {pinned} pinned in versions.toml")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        site = Path(tmp) / "site"
        build.build(site)
        run = subprocess.run(["node", str(ROOT / "scripts" / "inspector_pyodide.mjs"), pyodide_dir, str(site),
                              str(ROOT / "fixtures")], capture_output=True, text=True, check=True)
        browser = json.loads(run.stdout)
        reg = ROOT / "fixtures" / "golden" / "clean-patients.hl7"
        bad = 0
        for f in sorted((ROOT / "fixtures").glob("*/*.hl7")):
            text = f.read_text(encoding="utf-8")
            ctx = [(reg.name, reg.read_text())] if f.name != reg.name and "|ORU^" in text else []
            native = bridge.inspect(text, f.name, ctx, Path(tmp) / "native")
            if json.loads(json.dumps(native, ensure_ascii=False)) != browser[f.name]:
                print(f"DIFFERENT: {f.name}")
                bad += 1
        print(f"Pyodide {pinned} (Python {browser['_python']}): {len(browser) - 1 - bad} of "
              f"{len(browser) - 1} examples identical to the command line")
        return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
