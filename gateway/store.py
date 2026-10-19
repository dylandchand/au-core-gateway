"""Results store: every run's records, issues and fault outcomes in one SQLite file.

report.json describes one run; the store keeps them all, so runs can be compared. The main
use is the error diff: after a change to ingest, mapping or rules, which errors appeared and
which disappeared, record by record and field by field. A change that adds errors fails
`gateway diff` (exit 1), which is how CI can refuse an unintended change.

SQLite (Python standard library) keeps the tool to one command with nothing to install. The
schema is plain SQL with no SQLite-only features, so it moves to PostgreSQL unchanged if the
store ever needs to be shared.

The store copies the report; it never decides anything. `check()` recomputes the report's
headline numbers from the tables, so the two can't silently disagree.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DEFAULT = Path("out") / "history.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY,
    generated     TEXT NOT NULL,
    inputs        TEXT NOT NULL,          -- JSON list of input paths as shown in the report
    source_rules  TEXT NOT NULL,
    validator     TEXT,
    versions      TEXT,                   -- JSON
    substitutions INTEGER NOT NULL,       -- substitute packages in effect (0 = publishable)
    summary       TEXT NOT NULL,          -- JSON, as in report.json
    faults        TEXT                    -- JSON fault-detection summary, when a ledger was given
);
CREATE TABLE IF NOT EXISTS records (
    run_id         INTEGER NOT NULL REFERENCES runs(id),
    record_id      TEXT NOT NULL,
    kind           TEXT NOT NULL,
    status         TEXT NOT NULL,
    source_path    TEXT NOT NULL,
    linked_patient TEXT,
    PRIMARY KEY (run_id, record_id)
);
CREATE TABLE IF NOT EXISTS issues (
    run_id         INTEGER NOT NULL REFERENCES runs(id),
    record_id      TEXT NOT NULL,
    stage          TEXT NOT NULL,
    category       TEXT NOT NULL,
    message_id     TEXT NOT NULL,
    source_locator TEXT NOT NULL,
    expression     TEXT NOT NULL,
    resource       TEXT NOT NULL,
    message        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS issues_by_run_id ON issues (run_id, message_id);
CREATE INDEX IF NOT EXISTS issues_by_run_category ON issues (run_id, category);
CREATE TABLE IF NOT EXISTS fault_outcomes (
    run_id    INTEGER NOT NULL REFERENCES runs(id),
    record_id TEXT NOT NULL,
    fault     TEXT NOT NULL,
    at        TEXT NOT NULL,
    detected  INTEGER NOT NULL
);
"""

# What counts as a finding for diffs: errors, and warnings the gateway raises itself.
# (Validator background notes such as could-not-check are recorded but not diffed.)
FINDING = "(category = 'error' OR (category = 'warning' AND stage IN ('ingest', 'mapping', 'link')))"


def connect(path: Path = DEFAULT) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    return db


def save(db: sqlite3.Connection, report: dict) -> int:
    with db:
        cur = db.execute(
            "INSERT INTO runs (generated, inputs, source_rules, validator, versions, substitutions, summary, faults)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (report["generated"], json.dumps(report.get("inputs", [])), report["source_rules"],
             report.get("validator"), json.dumps(report.get("versions")), len(report.get("package_substitutions", [])),
             json.dumps(report["summary"]),
             json.dumps({k: v for k, v in report["faults"].items() if k != "faults"}) if "faults" in report else None))
        run_id = cur.lastrowid
        db.executemany(
            "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?)",
            [(run_id, r["record_id"], r["kind"], r["status"], r.get("source_path", ""), r.get("linked_patient"))
             for r in report["records"]])
        db.executemany(
            "INSERT INTO issues VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(run_id, r["record_id"], i["stage"], i["category"], i["message_id"], i.get("source_locator") or "",
              i.get("expression") or "", i.get("resource") or "", i["message"])
             for r in report["records"] for i in r["issues"]])
        if "faults" in report:
            db.executemany(
                "INSERT INTO fault_outcomes VALUES (?, ?, ?, ?, ?)",
                [(run_id, f["record_id"], f["fault"], f["at"], int(f["detected"])) for f in report["faults"]["faults"]])
    return run_id


def check(db: sqlite3.Connection, run_id: int) -> dict:
    """Headline numbers recomputed from the tables, in the report's own terms."""
    one = lambda sql, *a: db.execute(sql, (run_id, *a)).fetchone()[0]  # noqa: E731
    counts = {c: one("SELECT COUNT(*) FROM issues WHERE run_id = ? AND category = ?", c)
              for c in ("error", "could-not-check", "warning", "info")}
    return {
        "records": one("SELECT COUNT(*) FROM records WHERE run_id = ?"),
        "passed": one("SELECT COUNT(*) FROM records WHERE run_id = ? AND status = 'pass'"),
        "failed": one("SELECT COUNT(*) FROM records WHERE run_id = ? AND status = 'fail'"),
        **counts,
    }


def history(db: sqlite3.Connection, limit: int = 20) -> list[dict]:
    rows = db.execute("SELECT id, generated, inputs, substitutions, summary, faults FROM runs ORDER BY id DESC LIMIT ?",
                      (limit,)).fetchall()
    out = []
    for run_id, generated, inputs, subst, summary, faults in rows:
        s, f = json.loads(summary), json.loads(faults) if faults else None
        out.append({"id": run_id, "generated": generated, "inputs": json.loads(inputs), "substitutions": subst,
                    "records": s["records"], "passed": s["passed"], "errors": s["error"],
                    "faults": f"{f['detected']}/{f['injected']}" if f else ""})
    return out


def previous_run(db: sqlite3.Connection, run_id: int) -> int | None:
    """The latest earlier run over the same inputs."""
    inputs = db.execute("SELECT inputs FROM runs WHERE id = ?", (run_id,)).fetchone()
    if inputs is None:
        return None
    row = db.execute("SELECT MAX(id) FROM runs WHERE id < ? AND inputs = ?", (run_id, inputs[0])).fetchone()
    return row[0]


def latest_run(db: sqlite3.Connection) -> int | None:
    return db.execute("SELECT MAX(id) FROM runs").fetchone()[0]


def _findings(db: sqlite3.Connection, run_id: int) -> set[tuple[str, str, str, str]]:
    return set(db.execute(
        f"SELECT record_id, category, message_id, source_locator FROM issues WHERE run_id = ? AND {FINDING}",
        (run_id,)).fetchall())


def diff(db: sqlite3.Connection, old: int, new: int) -> dict:
    """Findings (errors and gateway warnings) that appeared or disappeared between two runs,
    matched on record, category, issue id and HL7 field."""
    a, b = _findings(db, old), _findings(db, new)
    as_dicts = lambda rows: [dict(zip(("record_id", "category", "id", "at"), r)) for r in sorted(rows)]  # noqa: E731
    return {"old": old, "new": new, "appeared": as_dicts(b - a), "resolved": as_dicts(a - b),
            "unchanged": len(a & b)}
