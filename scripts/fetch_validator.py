"""Download the official HL7 FHIR Validator jar into tools/ and record its version.

Usage:  python scripts/fetch_validator.py
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
JAR = TOOLS / "validator_cli.jar"
BASE = "https://github.com/hapifhir/org.hl7.fhir.core/releases"


def main() -> int:
    release = tomllib.loads((ROOT / "versions.toml").read_text())["validator"]["release"]
    url = (f"{BASE}/latest/download/validator_cli.jar" if release == "latest"
           else f"{BASE}/download/{release}/validator_cli.jar")
    TOOLS.mkdir(exist_ok=True)
    if not JAR.exists():
        print(f"Downloading validator ({release})...")
        urllib.request.urlretrieve(url, JAR)
    out = subprocess.run(["java", "-jar", str(JAR), "-version"],
                         capture_output=True, text=True, timeout=120)
    version = next((line.strip() for line in (out.stdout + out.stderr).splitlines()
                    if "FHIR Validation tool" in line), "unknown")
    (TOOLS / "validator.version").write_text(version + "\n")
    print(version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
