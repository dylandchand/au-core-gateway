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
    args = parser.parse_args(argv)

    report = pipeline.run(args.inputs, args.source, args.out)
    pipeline.print_summary(report)
    print(f"\nFull report: {args.out / 'report.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
