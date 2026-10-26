"""Proves the Interface Monitor acknowledges, inside Pyodide, exactly as the command line does.

Builds the monitor page, replays its feed and then each staged message fault through one
Session in Pyodide under Node (scripts/monitor_pyodide.mjs), and compares every receipt (ACK
code, text, the ACK message itself, issues) with the same messages through a Session on this
machine. Both sides use the same fixed clock, so the ACKs must be byte for byte the same.

Usage:  python scripts/check_monitor.py <pyodide dir> [feed dir]
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gateway.interface.session import Session  # noqa: E402
from gateway.monitor import build  # noqa: E402

CLOCK = datetime(2026, 10, 19, 9, 0, tzinfo=timezone(timedelta(hours=10)))


def main(pyodide_dir: str, feed_dir: str | None = None) -> int:
    pinned = tomllib.loads((ROOT / "versions.toml").read_text())["inspector"]["pyodide"]
    found = json.loads((Path(pyodide_dir) / "package.json").read_text())["version"]
    if found != pinned:
        print(f"pyodide {found} found, {pinned} pinned in versions.toml")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        site = Path(tmp) / "monitor"
        build.build(site, Path(feed_dir) if feed_dir else None)
        run = subprocess.run(["node", str(ROOT / "scripts" / "monitor_pyodide.mjs"), pyodide_dir, str(site)],
                             capture_output=True, text=True, check=True)
        browser = json.loads(run.stdout)
        feed = build.feed(Path(feed_dir) if feed_dir else None)
        staged = [s for s in build.scenarios(Path(feed_dir) if feed_dir else None, feed) if s["kind"] == "send"]
        texts = [m["text"] for m in feed] + [t for s in staged for t in ([s["text"], s["then"]] if s.get("then") else [s["text"]])]
        s = Session(Path(tmp) / "native", clock=lambda: CLOCK)
        native = [json.loads(json.dumps(s.receive(t), ensure_ascii=False)) for t in texts]
        bad = [i for i, (a, b) in enumerate(zip(native, browser["receipts"])) if a != b]
        bad += list(range(len(browser["receipts"]), len(native)))
        for i in bad[:5]:
            print(f"DIFFERENT: message {i + 1}")
        codes = {c: sum(r["code"] == c for r in native) for c in ("AA", "AE", "AR")}
        # The feed's promise: with a Synthea feed it always shows an error and a rejection, every
        # planted message gets a non-clean reply, and nothing else does.
        if feed_dir:
            got = native[:len(feed)]
            feed_codes = {r["code"] for r in got}
            wrong = [m["text"].split("\r", 1)[0].split("|")[9] for m, r in zip(feed, got)
                     if bool(m.get("planted")) == (r["code"] == "AA" and not any(
                         i["category"] == "warning" for i in r["issues"]))]
            # "Lab report before its registration": held on arrival, filed when the registration comes.
            early = next((s for s in staged if s["id"] == "early"), None)
            if early:
                k = texts.index(early["text"], len(feed))
                first, then = native[k], native[k + 1]
                if not (first["held"] and any(r["n"] == first["n"] for r in then["released"])):
                    print("EARLY: the report was not held and then filed by its registration")
                    bad.append(-2)
            if not {"AE", "AR"} <= feed_codes or wrong:
                print(f"FEED: codes {sorted(feed_codes)}; planted/clean mismatches: {wrong[:5]}")
                bad.append(-1)
            else:
                print(f"Feed: {len(feed)} messages, {sum(1 for m in feed if m.get('planted'))} planted, "
                      f"each planted one flagged and nothing else")
        print(f"Pyodide {pinned} (Python {browser['python']}): {len(native) - len(bad)} of {len(native)} "
              f"acknowledgements identical to the command line ({codes})")
        return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
