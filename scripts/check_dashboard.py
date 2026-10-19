"""Sanity-check a generated dashboard: the data block parses as JSON and the page script
parses as JavaScript (node --check). Catches template mistakes without a browser.

Usage:  python scripts/check_dashboard.py out/dashboard.html
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    page = Path(sys.argv[1]).read_text(encoding="utf-8")
    data = re.search(r'<script type="application/json" id="gw-data">(.*?)</script>', page, re.S)
    if not data:
        print("no data block")
        return 1
    parsed = json.loads(data.group(1).replace("<\\/", "</"))
    scripts = re.findall(r"<script>(.*?)</script>", page, re.S)
    if not scripts:
        print("no page script")
        return 1
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(scripts[-1])
    result = subprocess.run(["node", "--check", fh.name], capture_output=True, text=True)
    Path(fh.name).unlink()
    if result.returncode != 0:
        print(result.stderr)
        return 1
    print(f"ok: {len(parsed['rows'])} records, {len(parsed['detail'])} with detail, "
          f"{len(page) / 1_000_000:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
