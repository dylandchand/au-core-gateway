"""Advisory design check of the built site pages with Impeccable's deterministic rules.

Runs `npx impeccable@<pinned> detect --json` on each page (version pinned in versions.toml),
lists every finding, and sets aside the few that are deliberate, each with its reason. It never
fails the build: these are design heuristics, a second opinion, not a conformance check.
In GitHub Actions, findings that are not set aside become warning annotations.

Usage:  python scripts/check_design.py site/index.html site/inspector/index.html
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (rule, text in the finding's snippet, why it stays)
DELIBERATE = [
    ("side-tab", ".json .ln.", "a coloured bar at the start of a code line marks that line (a problem, or the "
                               "one selected), as code editors do; it sits on a line, not a card"),
]


def deliberate(f: dict) -> str | None:
    for rule, text, why in DELIBERATE:
        if f.get("antipattern") == rule and text in f.get("snippet", ""):
            return why
    return None


def main(pages: list[str]) -> int:
    version = tomllib.loads((ROOT / "versions.toml").read_text())["design_check"]["impeccable"]
    gha = os.environ.get("GITHUB_ACTIONS") == "true"
    open_count = 0
    for page in pages:
        run = subprocess.run(["npx", "-y", f"impeccable@{version}", "detect", "--json", page],
                             capture_output=True, text=True)
        try:
            findings = json.loads(run.stdout or "[]")
        except json.JSONDecodeError:
            print(f"{page}: the design check did not run ({run.stderr.strip()[:200]})")
            continue
        kept = [(f, deliberate(f)) for f in findings]
        live = [f for f, why in kept if not why]
        open_count += len(live)
        print(f"{page}: {len(live)} finding(s), {len(findings) - len(live)} set aside as deliberate")
        for f, why in kept:
            line = f"  {f['severity']}: {f['name']} ({f['antipattern']}): {f.get('snippet', '')}"
            print(line + (f"  [deliberate: {why}]" if why else ""))
            if gha and not why:
                print(f"::warning title=Design check ({Path(page).parent.name or 'site'})::{f['name']}: {f.get('snippet', '')}")
    print(f"Impeccable {version}: {open_count} open finding(s) (advisory; never fails the build)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
