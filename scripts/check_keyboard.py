"""Keyboard check of the built site pages: everything can be reached and used without a mouse.

For each page (light theme, desktop width) it presses Tab from the top until focus comes back
round, and checks:
  - no trap: focus keeps moving and returns to the start of the page;
  - every stop shows where focus is (an outline or ring that is not there without focus);
  - the page's main controls are among the stops (KEY below);
  - the skip link is the first stop and moves focus to <main>.
Then it uses each page's main control with the keyboard alone (Enter or Space) and checks the
result appeared.

This is the mechanical half of a keyboard review. Whether the order makes sense to a person is
still judged by a person.

Usage:  python scripts/check_keyboard.py <site dir> <pyodide dir>
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_a11y import READY, router  # noqa: E402

MAX_STOPS = 600
KEY = {   # controls a keyboard user must be able to reach on each page
    "": ["#tech-toggle", "#theme-toggle, .theme-btn", "#ft-tabs [role=tab]", "#f-search"],
    "inspector/": ["#msg", "#run", ".ex", "#clear", "#tech-toggle"],
    "monitor/": ["#play", "#reset", "#chips .chip"],
}
# (what is used, keys pressed after focusing it, what must be true afterwards)
USE = {
    # Tabs follow the ARIA tabs pattern: one Tab stop for the set, arrow keys move between tabs.
    # The records list is one Tab stop too; the arrow keys move through it and show each record.
    "": [("#ft-tabs [aria-selected=true]", "ArrowRight", "document.getElementById('tab-ex').getAttribute('aria-selected') === 'true'"),
         ("#records-list .rec[tabindex='0']", "ArrowDown",
          "document.activeElement.matches('#records-list .rec') && document.activeElement.getAttribute('aria-current') === 'true'"
          " && document.activeElement !== document.querySelector('#records-list .rec')")],
    "inspector/": [(".ex >> nth=3", "Enter", "document.getElementById('editor-tag').textContent.length > 0 && !document.getElementById('results').hidden")],
    "monitor/": [("#chips .chip >> nth=0", "Enter", "document.querySelectorAll('#log .row').length > 0")],
}

FOCUS_STYLE = """(el) => {
  const s = getComputedStyle(el);
  return [s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0 ? s.outlineStyle + s.outlineWidth + s.outlineColor : '',
          s.boxShadow !== 'none' ? s.boxShadow : '', s.borderColor, s.backgroundColor].join('|');
}"""
DESCRIBE = """(el) => el.id ? '#' + el.id : el.tagName.toLowerCase() + (el.className && typeof el.className === 'string'
  ? '.' + el.className.trim().split(/\\s+/).join('.') : '') + (el.textContent ? ' "' + el.textContent.trim().slice(0, 30) + '"' : '')"""


def main(site: str, pyodide_dir: str) -> int:
    problems = 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for page_path, keys in KEY.items():
            name = page_path or "results"
            pg = browser.new_page(viewport={"width": 1280, "height": 900}, color_scheme="light")
            pg.route("**/*", router(Path(site), Path(pyodide_dir)))
            pg.goto(f"http://site/{page_path}index.html")
            pg.wait_for_function(READY[page_path], timeout=180_000)
            pg.wait_for_timeout(300)
            pg.evaluate("document.activeElement?.blur()")

            stops, unmarked, seen = [], [], set()
            for i in range(MAX_STOPS):
                pg.keyboard.press("Tab")
                el = pg.evaluate_handle("document.activeElement")
                if pg.evaluate("(el) => el === document.body || el === null", el):
                    break
                tag = pg.evaluate("(el) => { el.dataset.kbStop ??= String(Math.random()); return el.dataset.kbStop; }", el)
                if tag in seen:
                    break                       # came round to the first stop again
                seen.add(tag)
                desc = pg.evaluate(DESCRIBE, el)
                stops.append(desc)
                # A visible focus indicator: the element looks different focused than not.
                if pg.evaluate("(el) => el.matches(':focus-visible')", el):
                    focused = pg.evaluate(FOCUS_STYLE, el)
                    # Compare with the element's own style without focus: blur, measure, refocus.
                    pg.evaluate("(el) => el.blur()", el)
                    plain = pg.evaluate(FOCUS_STYLE, el)
                    pg.evaluate("(el) => el.focus({ focusVisible: true })", el)
                    if focused == plain:
                        unmarked.append(desc)
            else:
                print(f"{name}: focus never came back round after {MAX_STOPS} Tab presses (a trap or a very long page)")
                problems += 1

            first = stops[0] if stops else ""
            print(f"{name}: {len(stops)} Tab stops; first is {first}")
            if "skip" not in first.lower():
                print("  the first Tab stop is not the skip link")
                problems += 1
            for sel in keys:
                ok = pg.evaluate("(sel) => [...document.querySelectorAll(sel)].some(e => e.dataset.kbStop)", sel)
                if not ok:
                    print(f"  not reachable by Tab: {sel}")
                    problems += 1
            for desc in unmarked:
                print(f"  no visible focus indicator: {desc}")
            problems += len(unmarked)

            # The skip link moves focus into <main>.
            pg.evaluate("window.scrollTo(0, 0); document.activeElement?.blur()")
            pg.keyboard.press("Tab")
            pg.keyboard.press("Enter")
            pg.wait_for_timeout(200)
            if not pg.evaluate("document.getElementById('main').contains(document.activeElement) || document.activeElement.id === 'main'"):
                print("  the skip link does not move focus to the main content")
                problems += 1

            for target, key, check in USE[page_path]:
                pg.locator(target).focus()
                pg.keyboard.press(key)
                pg.wait_for_timeout(1500)
                if pg.evaluate(check):
                    print(f"  {target}: works from the keyboard ({key})")
                else:
                    print(f"  {target}: pressing {key} did not do its job")
                    problems += 1
            pg.close()
        browser.close()
    print(f"keyboard check: {problems} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sys.exit(main(*sys.argv[1:]))
