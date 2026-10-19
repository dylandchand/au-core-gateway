"""Prepare the package cache on a network that blocks packages.fhir.org (restricted sandboxes).

On a normal machine or in CI you do NOT need this: the validator downloads what it needs.

What it does: runs the validator once on an official AU Core example, and every time the
validator asks for a package version it cannot download, resolves it with
fetch_packages.py --allow-substitutes (an empty stub, or the nearest published release).
Repeats until the validator starts cleanly. Every substitution is logged in
~/.fhir/packages/SUBSTITUTIONS.txt.

Usage:  python scripts/bootstrap_offline.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JAR = ROOT / "tools" / "validator_cli.jar"
CACHE = Path.home() / ".fhir" / "packages"
EXAMPLE = CACHE / "hl7.fhir.au.core#2.0.0" / "package" / "example" / "Patient-banks-mia-leanne.json"
NEEDS = re.compile(r"(?:Unable to resolve package id|Installing) ([a-z0-9.\-]+#[0-9A-Za-z.\-?]+)")
# Requests for "latest" (#? / #null) of an R5-named package resolve to a stub of its pinned r4 twin.
LATEST_STUBS = {"hl7.terminology": "7.0.1", "hl7.fhir.uv.extensions": "5.2.0"}


def run(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True, cwd=ROOT).stdout


def main() -> int:
    run(sys.executable, "scripts/fetch_packages.py", "--allow-substitutes")
    for name, version in LATEST_STUBS.items():
        run(sys.executable, "scripts/fetch_packages.py", "--allow-substitutes", f"{name}#{version}")
    for attempt in range(1, 16):
        out = subprocess.run(
            ["java", "-Xmx3g", "-XX:+UseStringDeduplication", "-jar", str(JAR), str(EXAMPLE),
             "-version", "4.0.1", "-ig", "hl7.fhir.au.core#2.0.0", "-tx", "n/a"],
            capture_output=True, text=True, timeout=900)
        log = out.stdout + out.stderr
        match = NEEDS.search(log)
        if not match:
            result = next((l for l in log.splitlines() if l.startswith(("Success", "*FAILURE*"))), "")
            print(f"Validator starts cleanly after {attempt} run(s). Example: {result}")
            return 0 if result else 1
        wanted = match.group(1)
        print(f"  validator needs {wanted}")
        print(run(sys.executable, "scripts/fetch_packages.py", "--allow-substitutes", wanted)
              .strip().splitlines()[-1])
    print("Gave up after 15 attempts; see the validator output above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
