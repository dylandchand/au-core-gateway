"""Sanity-check a generated dashboard: the data block parses as JSON and the page script
parses as JavaScript (node --check), and every
index in the rows points at something that exists. Catches template mistakes without a browser.

Usage:  python scripts/check_dashboard.py out/dashboard.html
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path


ROW_LENGTH = 13   # see the row layout comment in template.html


def structure_problems(data: dict) -> list[str]:
    """Every index in a row must point at something that exists."""
    rows, files, issues = data["rows"], data["files"], data["issues"]
    out = []
    for r in rows:
        if len(r) != ROW_LENGTH:
            out.append(f"{r[0]}: row has {len(r)} columns, expected {ROW_LENGTH}")
            continue
        if not 0 <= r[7] < len(files):
            out.append(f"{r[0]}: file index {r[7]} out of range")
        if r[8] >= len(rows):
            out.append(f"{r[0]}: linked index {r[8]} out of range")
        if any(not 0 <= k < len(issues) for k in r[10]):
            out.append(f"{r[0]}: issue index out of range")
    ids = {r[0] for r in rows}
    for rid in data["detail"]:
        if rid not in ids:
            out.append(f"detail for unknown record {rid}")
    return out


def main() -> int:
    page = Path(sys.argv[1]).read_text(encoding="utf-8")
    data = re.search(r'<script type="application/json" id="gw-data">(.*?)</script>', page, re.S)
    if not data:
        print("no data block")
        return 1
    parsed = json.loads(data.group(1).replace("<\\/", "</"))
    problems = structure_problems(parsed)
    if problems:
        print("\n".join(problems[:20]))
        return 1
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
