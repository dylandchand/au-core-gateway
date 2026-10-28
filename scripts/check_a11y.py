"""Accessibility check of the built site pages with axe-core (Deque, MPL 2.0; pinned in versions.toml).

Loads each page in a headless browser, in light and dark themes and at phone width, and again
after the interactions that reveal hidden content (results, detail panels, tabs, the live feed),
then runs axe's WCAG 2.0, 2.1 and 2.2 level A and AA rules on what is on screen.

  - A WCAG finding axe rates "serious" or "critical" fails the check (exit 1).
  - "moderate" and "minor" WCAG findings, and axe's best-practice rules, are listed as advisory.
  - A finding may be set aside only in DELIBERATE below, with the reason.

What this does not prove: automated rules cover only part of WCAG. They cannot judge whether a
label makes sense, whether the reading order is logical, or how a screen reader user follows
the Monitor's live feed. A clean result here is a floor, not a certificate; the keyboard pass
(scripts/check_keyboard.py) and a person with a screen reader cover more.

Usage:  python scripts/check_a11y.py <site dir> <axe-core dir> <pyodide dir>
  <site dir> holds index.html (Results), inspector/ and monitor/, as published.
"""

from __future__ import annotations

import json
import os
import sys
import tomllib
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
WCAG = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]
FAIL = {"serious", "critical"}

# (rule id, text in the element's selector or HTML, why it stays). Empty: nothing is set aside.
DELIBERATE: list[tuple[str, str, str]] = []

VIEWS = [("light", 1280, 900, "light"), ("dark", 1280, 900, "dark"), ("phone", 390, 844, "light")]

# Interactions that reveal content, per page: (state name, JavaScript run in the page, ms to wait).
# Each runs after the previous one, so later states keep what earlier ones opened.
STATES = {
    "": [
        ("loaded", "", 300),
        ("technical detail on", "document.getElementById('tech-toggle')?.click()", 300),
        ("a planted mistake picked", "document.querySelector('#faults .mrow.fault')?.click()", 400),
        ("glossary and lists open", """
            document.querySelectorAll('details').forEach(d => d.open = true);
            document.getElementById('show-more')?.click();
            document.getElementById('tab-ex')?.click();""", 300),
        ("a problem opened", "document.querySelector('#causes button, #causes [role=button], #causes a')?.click()", 400),
        ("a record opened", "document.querySelector('#records-list button, #records-list a, #records-list [tabindex]')?.click()", 400),
    ],
    "inspector/": [
        ("results shown", "", 300),
        ("technical detail on", "document.getElementById('tech-toggle')?.click()", 300),
        ("a broken example", "document.querySelectorAll('.ex')[3]?.click()", 900),
        ("results out of date", """
            const m = document.getElementById('msg'); m.value += 'X';
            m.dispatchEvent(new Event('input', { bubbles: true }));""", 300),
    ],
    "monitor/": [
        ("ready", "", 300),
        ("feed running", "document.getElementById('sp-fast')?.click(); document.getElementById('play').click()", 6000),
        ("a message opened", """
            document.getElementById('play').click();
            document.querySelector('#log [role=button], #log button, #log tr[tabindex], #log li[tabindex]')?.click()""", 400),
        ("a fault staged", "document.querySelector('#chips .chip:not(:disabled)')?.click()", 3500),
    ],
}
READY = {
    "": "true",
    "inspector/": "document.getElementById('engine').classList.contains('ready')",
    "monitor/": "!document.getElementById('play').disabled",
}


def router(site: Path, pyodide: Path):
    types = {".html": "text/html", ".js": "application/javascript", ".mjs": "application/javascript",
             ".json": "application/json", ".wasm": "application/wasm", ".zip": "application/zip"}

    def handle(route):
        url = route.request.url
        if "cdn.jsdelivr.net/npm/pyodide@" in url:
            f = pyodide / url.split("?")[0].rsplit("/", 1)[-1]
        elif url.startswith("http://site/"):
            f = site / (url[len("http://site/"):].split("?")[0].split("#")[0])
            if f.is_dir():
                f = f / "index.html"
        else:
            return route.abort()          # nothing else is fetched (web fonts fall back)
        if not f.is_file():
            return route.fulfill(status=404, body="not found")
        return route.fulfill(path=str(f), content_type=types.get(f.suffix, "application/octet-stream"))
    return handle


def set_aside(rule: str, node: dict) -> str | None:
    where = " ".join(node.get("target", [])) + " " + node.get("html", "")
    for r, text, why in DELIBERATE:
        if r == rule and text in where:
            return why
    return None


def main(site: str, axe_dir: str, pyodide_dir: str) -> int:
    pinned = tomllib.loads((ROOT / "versions.toml").read_text())["accessibility_check"]["axe_core"]
    found = json.loads((Path(axe_dir) / "package.json").read_text())["version"]
    if found != pinned:
        print(f"axe-core {found} found, {pinned} pinned in versions.toml")
        return 1
    axe = (Path(axe_dir) / "axe.min.js").read_text()
    gha = os.environ.get("GITHUB_ACTIONS") == "true"
    findings: dict[tuple, dict] = {}       # (page, rule, element) -> finding, with every view/state seen

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for page_path, states in STATES.items():
            for view, w, h, scheme in VIEWS:
                pg = browser.new_page(viewport={"width": w, "height": h}, color_scheme=scheme)
                errors: list[str] = []
                pg.on("pageerror", lambda e: errors.append(str(e)[:200]))
                pg.route("**/*", router(Path(site), Path(pyodide_dir)))
                pg.goto(f"http://site/{page_path}index.html")
                pg.wait_for_function(READY[page_path], timeout=180_000)
                pg.add_script_tag(content=axe)
                for state, js, wait in states:
                    if js:
                        pg.evaluate(f"() => {{ {js} }}")
                    pg.wait_for_timeout(wait)
                    # Transitions finished (looping animations stopped), so colours are measured as they rest.
                    pg.evaluate("document.getAnimations().forEach(a => { try { a.finish(); } catch (e) { a.cancel(); } })")
                    res = pg.evaluate("""(tags) => axe.run(document, {
                        runOnly: { type: 'tag', values: tags.concat(['best-practice']) },
                        resultTypes: ['violations'] })""", WCAG)
                    for v in res["violations"]:
                        wcag = any(t in WCAG for t in v["tags"])
                        for node in v["nodes"]:
                            key = (page_path or "results", v["id"], " ".join(node["target"]))
                            f = findings.setdefault(key, {
                                "rule": v["id"], "help": v["help"], "impact": node.get("impact") or v.get("impact"),
                                "wcag": wcag, "html": node["html"][:160], "summary": node.get("failureSummary", ""),
                                "seen": [], "why": set_aside(v["id"], node)})
                            f["seen"].append(f"{view}/{state}")
                if errors:
                    print(f"{page_path or 'results'} ({view}): page errors: {errors}")
                pg.close()
        browser.close()

    failing = advisory = 0
    for (page, rule, target), f in sorted(findings.items()):
        fails = f["wcag"] and f["impact"] in FAIL and not f["why"]
        failing += fails
        advisory += not fails and not f["why"]
        grade = "FAIL" if fails else ("set aside" if f["why"] else "advisory")
        seen = ", ".join(sorted(set(f["seen"])))
        print(f"[{grade}] {page}: {rule} ({f['impact']}{', WCAG' if f['wcag'] else ', best practice'}) {f['help']}")
        print(f"    at {target}  ·  seen in {seen}")
        print(f"    {f['html']}")
        if f["why"]:
            print(f"    [deliberate: {f['why']}]")
        elif f["summary"]:
            print("    " + f["summary"].replace("\n", "\n    ")[:400])
        if gha and not f["why"]:
            kind = "error" if fails else "warning"
            print(f"::{kind} title=Accessibility ({page})::{rule}: {f['help']} at {target}")
    print(f"axe-core {pinned}: {failing} failing (serious or critical WCAG), {advisory} advisory, "
          f"{len(findings) - failing - advisory} set aside; {sum(len(s) for s in STATES.values()) * len(VIEWS)} page states scanned")
    return 1 if failing else 0


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    sys.exit(main(*sys.argv[1:]))
