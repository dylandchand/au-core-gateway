"""Command line entry point.

    python -m gateway run fixtures/golden fixtures/faults --source demo-lab --out out
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from gateway import pipeline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gateway", description="AU Core FHIR conformance gateway")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="convert HL7 v2 files and validate against AU Core")
    run.add_argument("inputs", nargs="+", type=Path, help=".hl7 files or folders")
    run.add_argument("--source", default="demo-lab", help="mapping rules file (gateway/mapping/rules/<name>.yaml)")
    run.add_argument("--out", type=Path, default=Path("out"), help="output folder")
    run.add_argument("--dashboard", action="store_true", help="also write out/dashboard.html")
    dash = sub.add_parser("dashboard", help="turn a report.json into a self-contained dashboard page")
    dash.add_argument("report", type=Path, help="path to report.json")
    dash.add_argument("--html", type=Path, default=None, help="output page (default: next to the report)")
    dash.add_argument("--fragment", action="store_true",
                      help="write the page body only (for hosts that add their own <html> skeleton)")
    args = parser.parse_args(argv)

    if args.command == "dashboard":
        return _dashboard(args.report, args.html or args.report.parent / "dashboard.html", args.fragment)

    report = pipeline.run(args.inputs, args.source, args.out)
    pipeline.print_summary(report)
    print(f"\nFull report: {args.out / 'report.json'}")
    if args.dashboard:
        _dashboard(args.out / "report.json", args.out / "dashboard.html", False)
    return 0


def _dashboard(report: Path, html: Path, fragment: bool) -> int:
    from gateway.dashboard.build import build
    data = build(report, html, fragment)
    size_mb = html.stat().st_size / 1_000_000
    print(f"Dashboard: {html} ({size_mb:.1f} MB, full detail for "
          f"{data['meta']['detail_records']} of {data['meta']['total_records']} records)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
