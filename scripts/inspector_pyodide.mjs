// Runs the built Message Inspector bundle (gateway.zip) inside Pyodide under Node, on every
// built-in example, and prints what the browser page would get. scripts/check_inspector.py
// compares it with the command line. Usage: node inspector_pyodide.mjs <pyodide dir> <site dir> <fixtures dir>
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const [pyodideDir, site, fixtures] = process.argv.slice(2);
const { loadPyodide } = await import(pathToFileURL(join(pyodideDir, "pyodide.mjs")).href);
const py = await loadPyodide({ indexURL: pyodideDir });
py.unpackArchive(new Uint8Array(readFileSync(join(site, "gateway.zip"))), "zip", { extractDir: "/app" });
py.runPython("import sys\nsys.path.insert(0, '/app')\nfrom gateway.inspector import bridge");
const bridge = py.globals.get("bridge");
const reg = readFileSync(join(fixtures, "golden", "clean-patients.hl7"), "utf8");
const out = {};
for (const group of ["golden", "faults"]) {
  for (const file of readdirSync(join(fixtures, group)).filter((f) => f.endsWith(".hl7")).sort()) {
    const text = readFileSync(join(fixtures, group, file), "utf8");
    const isLab = /\|ORU\^/.test(text);
    const ctx = file !== "clean-patients.hl7" && isLab ? [["clean-patients.hl7", reg]] : [];
    out[file] = JSON.parse(bridge.inspect_json(text, file, JSON.stringify(ctx)));
  }
}
out._python = py.runPython("import sys; sys.version.split()[0]");
process.stdout.write(JSON.stringify(out));
