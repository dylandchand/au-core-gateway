"""Generate a synthetic Queensland HL7 v2 feed: Synthea (pinned, seeded) -> exporter.

Same release + seed + size = byte-identical feeds on any machine (checked by comparing the
feed fingerprint printed at the end with the one CI publishes).

Usage:  python scripts/generate_population.py --patients 1000
Output: data/synthea-<N>/  (adt/*.hl7, oru/*.hl7, truth.json, source.json, synthea-csv/)
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gateway import provenance  # noqa: E402
JAR = ROOT / "tools" / "synthea-with-dependencies.jar"
REFERENCE_DATE = "20261019"


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
    # Two settings make the population identical on every machine, both found the hard way
    # (CI generated 1,156 patients where this machine generated 1,155 from the same seed):
    #   - the JVM timezone: Synthea's date arithmetic follows it, so Brisbane and UTC differ;
    #   - the end date: it defaults to "now", so a run next week would simulate further.
    cmd = ["java", "-Xmx3g", "-Duser.timezone=UTC", "-jar", str(JAR),
           "-s", seed, "-cs", seed, "-p", str(args.patients),
           "-r", REFERENCE_DATE,                  # reference date for ages and demographics
           "-e", REFERENCE_DATE,                  # simulation end date
           cfg["state"],
           f"--exporter.baseDirectory={work}",
           "--exporter.csv.export=true", "--exporter.fhir.export=false",
           "--exporter.hospital.fhir.export=false", "--exporter.practitioner.fhir.export=false",
           "--exporter.years_of_history=10", "--generate.log_patients.detail=none"]
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
    code = subprocess.run([sys.executable, str(ROOT / "scripts" / "synthea_to_hl7.py"),
                           "--csv", str(csv_dir), "--out", str(out), "--seed", seed]).returncode
    if code == 0:
        # Say what made this feed, so every page built from it can tell its readers.
        patients = len(json.loads((out / "truth.json").read_text()))
        manifest = provenance.feed_manifest(cfg["release"], cfg["seed"], cfg["state"], patients)
        (out / provenance.MANIFEST).write_text(json.dumps(manifest, indent=1) + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
