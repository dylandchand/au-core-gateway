"""Generate a synthetic Queensland HL7 v2 feed: Synthea (pinned, seeded) -> exporter.

Same release + seed + size = the same patients and messages, on any machine.

Usage:  python scripts/generate_population.py --patients 1000
Output: data/synthea-<N>/  (adt/*.hl7, oru/*.hl7, truth.json, synthea-csv/)
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JAR = ROOT / "tools" / "synthea-with-dependencies.jar"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--patients", type=int, default=1000, help="living patients to generate")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    cfg = tomllib.loads((ROOT / "versions.toml").read_text())["synthea"]
    out = args.out or ROOT / "data" / f"synthea-{args.patients}"
    work = out / "synthea-run"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    subprocess.run([sys.executable, str(ROOT / "scripts" / "fetch_synthea.py")], check=True)
    seed = str(cfg["seed"])
    cmd = ["java", "-Xmx3g", "-jar", str(JAR),
           "-s", seed, "-cs", seed, "-p", str(args.patients),
           "-r", "20261019",                      # fixed reference date: ages don't drift
           cfg["state"],
           f"--exporter.baseDirectory={work}",
           "--exporter.csv.export=true", "--exporter.fhir.export=false",
           "--exporter.hospital.fhir.export=false", "--exporter.practitioner.fhir.export=false",
           "--exporter.years_of_history=10", "--generate.log_patients.detail=none",
           # One thread: with several, the order patients are simulated in depends on the
           # machine's core count, and so does the population (seen in CI: 1,156 vs 1,155).
           "--generate.thread_pool_size=1"]
    print(f"Synthea {cfg['release']}: {args.patients} patients, seed {seed} ...")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stdout[-2000:], proc.stderr[-2000:])
        return proc.returncode

    csv_dir = out / "synthea-csv"
    if csv_dir.exists():
        shutil.rmtree(csv_dir)
    shutil.move(str(work / "csv"), csv_dir)
    shutil.rmtree(work)
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "synthea_to_hl7.py"),
                           "--csv", str(csv_dir), "--out", str(out), "--seed", seed]).returncode


if __name__ == "__main__":
    sys.exit(main())
