"""Command line entry point.

    python -m gateway run fixtures/golden fixtures/faults --source demo-lab --out out
    python -m gateway history              # runs recorded in out/history.db
    python -m gateway diff                 # errors that appeared or went away since the last
                                           # run over the same inputs (exit 1 if any appeared)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from gateway import pipeline, store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gateway", description="AU Core FHIR conformance gateway")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="convert HL7 v2 files and validate against AU Core")
    run.add_argument("inputs", nargs="+", type=Path, help=".hl7 files or folders")
    run.add_argument("--source", default="demo-lab", help="mapping rules file (gateway/mapping/rules/<name>.yaml)")
    run.add_argument("--out", type=Path, default=Path("out"), help="output folder")
    run.add_argument("--dashboard", action="store_true", help="also write out/dashboard.html")
    run.add_argument("--validate-all-observations", action="store_true", default=None,
                     help="validate every Observation, however large the run (slow at scale)")
    run.add_argument("--observation-sample", type=int, default=pipeline.SAMPLE_PER_THOUSAND, metavar="N",
                     help="in large runs, validate every result shape plus N per 1000 others (default %(default)s)")
    run.add_argument("--faults", type=Path, default=None, metavar="LEDGER",
                     help="faults.json from scripts/inject_faults.py: measure detection against it")
    run.add_argument("--store", type=Path, default=store.DEFAULT,
                     help="results store to record the run in (default %(default)s)")
    run.add_argument("--no-store", action="store_true", help="do not record the run")
    hist = sub.add_parser("history", help="list recorded runs")
    hist.add_argument("--store", type=Path, default=store.DEFAULT)
    hist.add_argument("--limit", type=int, default=20)
    df = sub.add_parser("diff", help="errors that appeared or were resolved between two runs (exit 1 if any appeared)")
    df.add_argument("old", type=int, nargs="?", help="run id (default: the run before NEW over the same inputs)")
    df.add_argument("new", type=int, nargs="?", help="run id (default: the latest run)")
    df.add_argument("--store", type=Path, default=store.DEFAULT)
    df.add_argument("--show", type=int, default=30, help="findings listed per side (default %(default)s)")
    dash = sub.add_parser("dashboard", help="turn a report.json into a self-contained dashboard page")
    dash.add_argument("report", type=Path, help="path to report.json")
    dash.add_argument("--html", type=Path, default=None, help="output page (default: next to the report)")
    dash.add_argument("--fragment", action="store_true",
                      help="write the page body only (for hosts that add their own <html> skeleton)")
    dash.add_argument("--inputs", type=Path, nargs="+", default=None,
                      help="the .hl7 files or folders the run read, if they are outside this folder "
                           "(so the page can show the lab's original lines)")
    args = parser.parse_args(argv)

    if args.command == "dashboard":
        return _dashboard(args.report, args.html or args.report.parent / "dashboard.html", args.fragment,
                          pipeline.source_locations(args.inputs) if args.inputs else None)
    if args.command == "history":
        return _history(args.store, args.limit)
    if args.command == "diff":
        return _diff(args.store, args.old, args.new, args.show)

    report = pipeline.run(args.inputs, args.source, args.out, args.validate_all_observations,
                          args.observation_sample, args.faults)
    pipeline.print_summary(report)
    print(f"\nFull report: {args.out / 'report.json'}")
    if not args.no_store:
        db = store.connect(args.store)
        run_id = store.save(db, report)
        print(f"Recorded as run {run_id} in {args.store} (compare with: python -m gateway diff)")
    if args.dashboard:
        _dashboard(args.out / "report.json", args.out / "dashboard.html", False,
                   pipeline.source_locations(args.inputs))
    return 0


def _history(path: Path, limit: int) -> int:
    if not path.exists():
        print(f"no runs recorded yet ({path})")
        return 0
    rows = store.history(store.connect(path), limit)
    print(f"{'run':>4}  {'generated (UTC)':<25} {'records':>8} {'passed':>8} {'errors':>7} {'faults':>9}  inputs")
    for r in rows:
        flag = "  (substituted packages)" if r["substitutions"] else ""
        print(f"{r['id']:>4}  {r['generated']:<25} {r['records']:>8} {r['passed']:>8} {r['errors']:>7} "
              f"{r['faults']:>9}  {' '.join(r['inputs'])}{flag}")
    return 0


def _diff(path: Path, old: int | None, new: int | None, show: int) -> int:
    if not path.exists():
        print(f"no runs recorded yet ({path})")
        return 2
    db = store.connect(path)
    new = new or store.latest_run(db)
    old = old or (store.previous_run(db, new) if new else None)
    if not new or not old:
        print("need two runs over the same inputs to compare (or pass two run ids)")
        return 2
    d = store.diff(db, old, new)
    print(f"Run {old} -> run {new}: {len(d['appeared'])} findings appeared, {len(d['resolved'])} resolved, "
          f"{d['unchanged']} unchanged (errors and gateway warnings, by record, id and field)")
    for title, rows in (("Appeared", d["appeared"]), ("Resolved", d["resolved"])):
        if rows:
            print(f"\n{title}:")
            for f in rows[:show]:
                print(f"  {f['category']:<8} {f['record_id']:<34} {f['at']:<12} {f['id']}")
            if len(rows) > show:
                print(f"  ... and {len(rows) - show} more")
    return 1 if d["appeared"] else 0


def _dashboard(report: Path, html: Path, fragment: bool, sources: dict[str, Path] | None = None) -> int:
    from gateway.dashboard.build import build
    data = build(report, html, fragment, sources)
    size_mb = html.stat().st_size / 1_000_000
    print(f"Dashboard: {html} ({size_mb:.1f} MB, full detail for "
          f"{data['meta']['detail_records']} of {data['meta']['total_records']} records)")
    if data['meta'].get("source_lines_missing"):
        print(f"Note: the lab's original lines were not found for {data['meta']['source_lines_missing']} record(s); "
              "the page shows their findings only. Rebuild with: python -m gateway dashboard "
              f"{report} --inputs <the .hl7 files or folders>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
