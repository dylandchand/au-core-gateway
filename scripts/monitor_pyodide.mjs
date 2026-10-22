// Runs the built Interface Monitor bundle (gateway.zip) inside Pyodide under Node: the page's
// feed, then each staged message fault, through one Session with a fixed clock, and prints the
// receipts (ACKs included). scripts/check_monitor.py compares them with the command line.
// Usage: node monitor_pyodide.mjs <pyodide dir> <monitor dir>
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const [pyodideDir, site] = process.argv.slice(2);
const html = readFileSync(join(site, "index.html"), "utf8");
const data = JSON.parse(/<script id="mon-data" type="application\/json">([\s\S]*?)<\/script>/.exec(html)[1]);
const { loadPyodide } = await import(pathToFileURL(join(pyodideDir, "pyodide.mjs")).href);
const py = await loadPyodide({ indexURL: pyodideDir });
py.unpackArchive(new Uint8Array(readFileSync(join(site, "gateway.zip"))), "zip", { extractDir: "/app" });
py.runPython([
  "import sys, json", "sys.path.insert(0, '/app')",
  "from datetime import datetime, timedelta, timezone",
  "from gateway.interface.session import Session",
  "S = Session(clock=lambda: datetime(2026, 10, 19, 9, 0, tzinfo=timezone(timedelta(hours=10))))",
  "def receive_json(text):", "    return json.dumps(S.receive(text))",
].join("\n"));
const receive = py.globals.get("receive_json");
const texts = data.feed.map((m) => m.text).concat(data.scenarios.filter((s) => s.kind === "send").map((s) => s.text));
const out = { receipts: texts.map((t) => JSON.parse(receive(t))), python: py.runPython("import sys; sys.version.split()[0]") };
process.stdout.write(JSON.stringify(out));
