"""Download the pinned Synthea release jar into tools/.

Usage:  python scripts/fetch_synthea.py
"""

from __future__ import annotations

import sys
import tomllib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JAR = ROOT / "tools" / "synthea-with-dependencies.jar"


def main() -> int:
    release = tomllib.loads((ROOT / "versions.toml").read_text())["synthea"]["release"]
    stamp = ROOT / "tools" / "synthea.version"
    if JAR.exists() and stamp.exists() and stamp.read_text().strip() == release:
        print(f"Synthea {release} already present")
        return 0
    JAR.parent.mkdir(exist_ok=True)
    url = f"https://github.com/synthetichealth/synthea/releases/download/{release}/synthea-with-dependencies.jar"
    print(f"Downloading Synthea {release}...")
    urllib.request.urlretrieve(url, JAR)
    stamp.write_text(release + "\n")
    print(f"Saved {JAR.relative_to(ROOT)} ({JAR.stat().st_size // 1_000_000} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
