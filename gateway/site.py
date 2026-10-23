"""The site's shared layout: one header, one "on this page" bar and one footer for every page.

Both pages (the dashboard, gateway/dashboard/template.html, and the Message Inspector,
gateway/inspector/page.html) take their header, footer, layout styles and navigation script
from here, so the site reads as one site and the two can never drift apart:

  - Site header (sticky): the brand, which goes home, and the site's pages. The current page is
    marked (aria-current="page"); links that leave the site say so (↗ and a hidden label).
    At the right, a light/dark button. At phone width the pages fold into a Menu button.
  - "On this page" bar, under the header, on pages with sections: links to each section, the
    one in view marked, plus the page's own controls (the Technical detail switch). At phone
    width it becomes a "Jump to" button that lists every section, so none is hidden off-screen.
  - Site footer: what the project is, the pages again, project links, and the legal notices.

Cross-page links use the published site's address, so a dashboard built locally (make demo)
still reaches the live Message Inspector. The current page links to its own top instead.

Pages use the placeholders __SITE_CSS__, __SITE_HEADER__, __SITE_FOOTER__ and __SITE_JS__;
fill() replaces them. The palette (colour, type and radius tokens) is defined here too, once, for every page.
"""

from __future__ import annotations

from html import escape

from gateway import provenance

SITE_URL = "https://dylandchand.github.io/au-core-gateway/"
REPO_URL = "https://github.com/dylandchand/au-core-gateway"

# (key, label, address): the site's own pages, in reading order.
PAGES = [
    ("results", "Results", SITE_URL),
    ("inspector", "Message Inspector", SITE_URL + "inspector/"),
    ("monitor", "Interface Monitor", SITE_URL + "monitor/"),
]
# (label, address, where it goes): pages elsewhere, opened from the header and footer.
ELSEWHERE = [
    ("Write-up", REPO_URL + "/blob/main/docs/WRITEUP.md", "on GitHub"),
    ("Code", REPO_URL, "on GitHub"),
]
FOOTER_EXTRA = [
    ("Latest release", REPO_URL + "/releases/latest", "on GitHub"),
]

LEGAL = (
    "This material contains content from LOINC (<a href=\"http://loinc.org\">http://loinc.org</a>). "
    "LOINC is copyright © Regenstrief Institute, Inc. and the Logical Observation Identifiers Names and "
    "Codes (LOINC) Committee and is available at no cost under the license at "
    "<a href=\"http://loinc.org/license\">http://loinc.org/license</a>. LOINC® is a registered United "
    "States trademark of Regenstrief Institute, Inc. FHIR® is the registered trademark of HL7 and is used "
    "with the permission of HL7. HL7® is a registered trademark of Health Level Seven International. "
    "This project is not affiliated with, or approved or sponsored by, HL7, HL7 Australia, the Australian "
    "Digital Health Agency, Services Australia, Regenstrief Institute or MITRE."
)

BRAND_ICON = ('<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 5h6M3 10h4M3 15h6" stroke="currentColor" '
              'stroke-width="1.8" stroke-linecap="round"/><path d="M11 10h3" stroke="currentColor" stroke-width="1.8" '
              'stroke-linecap="round"/><path d="M12.5 6.5L16 10l-3.5 3.5" fill="none" stroke="currentColor" '
              'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>')

# Light or dark: follows the device until the visitor chooses, then remembers the choice on
# that device (localStorage "gw-theme", shared by every page). THEME_EARLY runs before the page
# paints, so a chosen theme never flashes the other one first.
THEME_EARLY = ('(function(){try{var t=localStorage.getItem("gw-theme");'
               'if(t==="light"||t==="dark")document.documentElement.setAttribute("data-theme",t)}catch(e){}})();')
THEME_BUTTON = ('<button class="theme-btn" id="theme-toggle" type="button" aria-label="Switch to dark mode" '
                'title="Switch to dark mode">'
                '<svg class="i-moon" viewBox="0 0 20 20" aria-hidden="true"><path d="M16.5 12.2A6.6 6.6 0 0 1 7.8 3.5a6.6 6.6 0 1 0 8.7 8.7z" '
                'fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>'
                '<svg class="i-sun" viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="3.4" fill="none" '
                'stroke="currentColor" stroke-width="1.6"/><path d="M10 2.2v2M10 15.8v2M2.2 10h2M15.8 10h2M4.5 4.5l1.4 1.4'
                'M14.1 14.1l1.4 1.4M4.5 15.5l1.4-1.4M14.1 5.9l1.4-1.4" stroke="currentColor" stroke-width="1.6" '
                'stroke-linecap="round"/></svg></button>')

TECH_SWITCH = ('<button class="switch" id="tech-toggle" type="button" role="switch" aria-checked="false" '
               'title="Show codes, field paths and validator ids"><span class="track" aria-hidden="true"></span>'
               '<span>Technical<span class="sw-x"> detail</span></span></button>')


def _out(label: str, href: str, where: str, cls: str = "") -> str:
    return (f'<a href="{escape(href)}" class="out{(" " + cls) if cls else ""}" rel="noopener">'
            f'{escape(label)}<span aria-hidden="true"> ↗</span><span class="sr"> ({escape(where)})</span></a>')


def _page_link(key: str, label: str, href: str, current: str) -> str:
    if key == current:
        return f'<a href="#top" aria-current="page">{escape(label)}</a>'
    return f'<a href="{escape(href)}">{escape(label)}</a>'


def header(current: str, sections: list[tuple[str, str, str | None]] = (), tools: str = "") -> str:
    """current: a PAGES key. sections: (anchor id, label, link id or None); a link given an id
    can be hidden (attribute hidden) until its section appears. tools: the page's own controls."""
    items = "".join(f"<li>{_page_link(k, label, href, current)}</li>" for k, label, href in PAGES)
    items += "".join(f"<li>{_out(label, href, where)}</li>" for label, href, where in ELSEWHERE)
    head = f'''<script>{THEME_EARLY}</script>
<a class="skip" href="#main">Skip to content</a>
<header class="site-head" id="site-head">
  <div class="site-head-in">
    <a class="brand" href="{escape(SITE_URL if current != "results" else "#top")}">{BRAND_ICON}AU Core Gateway</a>
    <div class="head-right">
      <nav class="site-nav" aria-label="Site">
        <button class="menu-btn" type="button" aria-expanded="false" aria-controls="site-links">
          <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 6h14M3 10h14M3 14h14" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>Menu</button>
        <ul id="site-links">{items}</ul>
      </nav>
      {THEME_BUTTON}
    </div>
  </div>'''
    if sections:
        def link(anchor: str, label: str, lid: str | None) -> str:
            extra = f' id="{escape(lid)}" hidden' if lid else ""
            return f'<li><a href="#{escape(anchor)}"{extra}>{escape(label)}</a></li>'
        links = "".join(link(*s) for s in sections)
        first = escape(sections[0][1])
        head += f'''
  <div class="page-bar">
    <div class="page-bar-in">
      <nav class="page-nav" id="page-nav" aria-label="On this page">
        <button class="jump" type="button" aria-expanded="false" aria-controls="page-links">
          <span class="jump-k">Jump to</span><span class="jump-v" id="jump-current">{first}</span>
          <svg viewBox="0 0 12 12" aria-hidden="true"><path d="M3 4.5 6 7.5 9 4.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg></button>
        <ul id="page-links">{links}</ul>
      </nav>
      <div class="page-tools">{tools}</div>
    </div>
  </div>'''
    return head + "\n</header>"


def footer(current: str, about: str) -> str:
    """about: one or two sentences about this page, shown first."""
    pages = "".join(f"<li>{_page_link(k, label, href, current)}</li>" for k, label, href in PAGES)
    project = "".join(f"<li>{_out(label, href, where)}</li>" for label, href, where in ELSEWHERE + FOOTER_EXTRA)
    return f'''<footer class="site-foot">
  <div class="site-foot-in">
    <div class="foot-grid">
      <div class="foot-about">
        <p class="foot-brand">{BRAND_ICON}AU Core Gateway</p>
        <p>Turns the HL7 v2 messages a pathology lab sends into Australia's national FHIR standard, AU Core, and has
          HL7's official validator check every record. {about}</p>
        <p>Synthetic data only. Not certified and not for clinical use. Do not run real patient data through this project.</p>
      </div>
      <nav aria-label="Pages"><h2 class="foot-h">Pages</h2><ul>{pages}</ul></nav>
      <nav aria-label="Project"><h2 class="foot-h">Project</h2><ul>{project}</ul></nav>
    </div>
    <p class="legal">{LEGAL}</p>
  </div>
</footer>'''


CSS = r"""

/* ---------- Palette (gateway/site.py, the one set of tokens for every page) ----------
   Black and white: neutral greys with no tint, black as the accent (off-white in dark themes).
   Colour appears only where it means something: red error, amber warning, green pass,
   blue "couldn't check". Code panels follow the theme, with syntax colour that avoids those
   hues: violet for names (JSON keys, HL7 segments), cyan for values, grey for punctuation.
   Dark themes follow the usual practice: a dark grey ground rather than black, off-white
   text rather than white, raised surfaces a step lighter (shadows barely show in the dark),
   and softer, less saturated colours. */
:root {
  --bg: #fafafa; --surface: #ffffff; --surface-2: #f4f4f5; --ink: #09090b; --ink-2: #3f3f46; --muted: #71717a;
  --line: #e4e4e7; --line-2: #d4d4d8; --accent: #18181b; --accent-2: #3f3f46; --accent-soft: #f4f4f5;
  --code-bg: #f4f4f5; --code-key: #6d28d9; --code-val: #0e7490; --code-dim: #66666e;
  --bad: #c4373a; --bad-soft: #fbe8e7; --warn: #8f6200; --warn-mark: #f0a500; --warn-soft: #fdf2d8;
  --good: #187a33; --good-soft: #e2f2e5; --cnc: #1f63b8; --cnc-mark: #2a78d6; --cnc-soft: #e3edfa; --info-mark: #a1a1aa;
  --shadow-1: 0 1px 2px rgb(0 0 0 / 0.04); --shadow-2: 0 6px 12px -4px rgb(0 0 0 / 0.10), 0 1px 3px rgb(0 0 0 / 0.06);
  --font-ui: "IBM Plex Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
  --font-display: "IBM Plex Sans Condensed", "IBM Plex Sans", system-ui, sans-serif;
  --font-code: "IBM Plex Mono", ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace;
  --r-lg: 12px; --r: 8px; --r-sm: 5px;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #111113; --surface: #18181b; --surface-2: #222226; --ink: #ececee; --ink-2: #c9c9ce; --muted: #9d9da6;
    --line: #2a2a2f; --line-2: #3d3d44; --accent: #ececee; --accent-2: #d4d4d8; --accent-soft: #2a2a2f;
    --code-bg: #1f1f23; --code-key: #c4b5fd; --code-val: #a5f3fc; --code-dim: #8b8b94;
    --bad: #ef7a77; --bad-soft: #3a1c1d; --warn: #f2bb44; --warn-mark: #f6b11c; --warn-soft: #362c12;
    --good: #45c466; --good-soft: #142e1c; --cnc: #74a9ef; --cnc-mark: #3f8be6; --cnc-soft: #162943; --info-mark: #52525b;
    --shadow-1: 0 1px 2px rgb(0 0 0 / 0.5); --shadow-2: 0 6px 12px -4px rgb(0 0 0 / 0.6), 0 1px 3px rgb(0 0 0 / 0.4);
    color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --bg: #111113; --surface: #18181b; --surface-2: #222226; --ink: #ececee; --ink-2: #c9c9ce; --muted: #9d9da6;
  --line: #2a2a2f; --line-2: #3d3d44; --accent: #ececee; --accent-2: #d4d4d8; --accent-soft: #2a2a2f;
  --code-bg: #1f1f23; --code-key: #c4b5fd; --code-val: #a5f3fc; --code-dim: #8b8b94;
  --bad: #ef7a77; --bad-soft: #3a1c1d; --warn: #f2bb44; --warn-mark: #f6b11c; --warn-soft: #362c12;
  --good: #45c466; --good-soft: #142e1c; --cnc: #74a9ef; --cnc-mark: #3f8be6; --cnc-soft: #162943; --info-mark: #52525b;
  --shadow-1: 0 1px 2px rgb(0 0 0 / 0.5); --shadow-2: 0 6px 12px -4px rgb(0 0 0 / 0.6), 0 1px 3px rgb(0 0 0 / 0.4);
  color-scheme: dark;
}
/* ---------- Site layout (gateway/site.py, shared by every page) ---------- */
:root { --head-h: 60px; }
html { scroll-padding-top: calc(env(safe-area-inset-top, 0px) + var(--head-h) + 16px); }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
.skip { position: absolute; left: -9999px; top: 8px; z-index: 50; background: var(--surface); color: var(--ink);
  padding: 8px 12px; border-radius: var(--r); box-shadow: 0 2px 8px rgb(0 0 0 / 0.2); }
.skip:focus { left: max(16px, env(safe-area-inset-left, 0px)); top: calc(env(safe-area-inset-top, 0px) + 8px); }
/* Problems in code, the same everywhere (as on the Results page): the field is outlined and tinted,
   red for an error and amber for a warning, and its line carries a bar in the same colour. */
.hl-error, .hl-warning { border-radius: 2px; color: var(--ink); -webkit-box-decoration-break: clone; box-decoration-break: clone; }
.hl-error { background: var(--bad-soft); box-shadow: inset 0 0 0 1.5px var(--bad); }
.hl-warning { background: var(--warn-soft); box-shadow: inset 0 0 0 1.5px var(--warn-mark); }
.hl-error .sep, .hl-warning .sep { color: inherit; }
.ln-error { background: color-mix(in srgb, var(--bad-soft) 55%, transparent); box-shadow: inset 3px 0 0 var(--bad); }
.ln-warning { background: color-mix(in srgb, var(--warn-soft) 55%, transparent); box-shadow: inset 3px 0 0 var(--warn-mark); }
body::before { content: ""; position: fixed; inset: 0 0 auto; height: env(safe-area-inset-top, 0px); z-index: 30; background: var(--bg); }

.site-head { position: sticky; top: env(safe-area-inset-top, 0px); z-index: 20;
  margin-inline: calc(-1 * max(16px, env(safe-area-inset-left, 0px))) calc(-1 * max(16px, env(safe-area-inset-right, 0px)));
  background: color-mix(in srgb, var(--bg) 90%, transparent);
  backdrop-filter: saturate(1.4) blur(10px); -webkit-backdrop-filter: saturate(1.4) blur(10px); }
/* content-box: the inner 1200px lines up exactly with each page's 1200px content column. */
.site-head-in, .page-bar-in, .site-foot-in { box-sizing: content-box; max-width: 1200px; margin: 0 auto;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }
.site-head { border-bottom: 1px solid var(--line); }
.site-head-in { display: flex; align-items: center; justify-content: space-between; gap: 12px 24px; min-height: 56px; }
.site-head .brand, .foot-brand { font-family: var(--font-display); font-weight: 600; font-size: 1.0625rem; white-space: nowrap;
  display: inline-flex; align-items: center; gap: 10px; color: var(--ink); text-decoration: none; margin: 0; }
.site-head .brand svg, .foot-brand svg { width: 22px; height: 22px; flex: none; color: var(--accent); }
.site-nav ul, .page-nav ul, .site-foot ul { list-style: none; margin: 0; padding: 0; }
.site-nav ul { display: flex; align-items: center; gap: 4px; }
.site-nav a { display: inline-flex; align-items: center; min-height: 36px; padding: 6px 12px; border-radius: var(--r);
  color: var(--ink-2); text-decoration: none; font-size: 0.9375rem; font-weight: 500; white-space: nowrap; }
.site-nav a:hover { color: var(--ink); background: var(--surface-2); }
.site-nav a[aria-current="page"] { color: var(--ink); box-shadow: inset 0 -2px 0 var(--accent); border-radius: 0; }
.site-nav a.out, .page-nav a.out { color: var(--muted); font-weight: 400; }
.head-right { display: flex; align-items: center; gap: 8px; }
.theme-btn { display: inline-grid; place-items: center; width: 36px; height: 36px; border-radius: var(--r); flex: none;
  border: 1px solid var(--line); background: var(--surface); color: var(--ink-2); cursor: pointer; padding: 0; }
.theme-btn:hover { color: var(--ink); background: var(--surface-2); }
.theme-btn svg { width: 18px; height: 18px; }
.theme-btn .i-sun { display: none; }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) .theme-btn .i-sun { display: block; }
  :root:not([data-theme="light"]) .theme-btn .i-moon { display: none; }
}
:root[data-theme="dark"] .theme-btn .i-sun { display: block; }
:root[data-theme="dark"] .theme-btn .i-moon { display: none; }
.menu-btn { display: none; align-items: center; gap: 8px; border: 1px solid var(--line); background: var(--surface); color: var(--ink);
  border-radius: var(--r); padding: 6px 12px; font: 500 0.9375rem/1 var(--font-ui, inherit); cursor: pointer; min-height: 36px; }
.menu-btn svg { width: 18px; height: 18px; }

.page-bar { border-top: 1px solid var(--line); padding-top: 6px; padding-bottom: 6px; }
.page-bar-in { display: flex; align-items: center; justify-content: space-between; gap: 8px 20px; min-height: 36px; }
.page-nav { min-width: 0; flex: 1; position: relative; }
.page-nav ul { display: flex; flex-wrap: wrap; gap: 2px 4px; align-items: center; }
.page-nav li:has(> a[hidden]) { display: none; }
.page-nav a { display: inline-flex; align-items: center; min-height: 32px; padding: 4px 10px; border-radius: 999px;
  color: var(--ink-2); text-decoration: none; font-size: 0.875rem; white-space: nowrap; }
.page-nav a:hover { color: var(--ink); background: var(--surface-2); }
.page-nav a[aria-current="location"] { color: var(--accent); background: var(--accent-soft); font-weight: 600; }
.jump { display: none; align-items: center; gap: 8px; border: 0; background: none; color: var(--ink); cursor: pointer;
  padding: 8px 0; font: inherit; font-size: 0.9375rem; min-width: 0; }
.jump-k { color: var(--muted); font-size: 0.8125rem; white-space: nowrap; }
.jump-v { font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.jump svg { width: 12px; height: 12px; flex: none; transition: transform 150ms; }
.jump[aria-expanded="true"] svg { transform: rotate(180deg); }
.page-tools { display: flex; align-items: center; gap: 12px; flex: none; }

.site-foot { margin-top: 96px; border-top: 1px solid var(--line); background: var(--surface);
  margin-inline: calc(-1 * max(16px, env(safe-area-inset-left, 0px))) calc(-1 * max(16px, env(safe-area-inset-right, 0px)));
  margin-bottom: -64px; padding-block: 40px 48px; color: var(--ink-2); font-size: 0.875rem; }
.site-foot-in { display: grid; gap: 28px; }
.foot-grid { display: grid; grid-template-columns: minmax(0, 2fr) minmax(0, 1fr) minmax(0, 1fr); gap: 28px 40px; }
.foot-about { display: grid; gap: 10px; max-width: 60ch; }
.foot-h { font-family: var(--font-ui, inherit); font-size: 0.75rem; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase;
  color: var(--muted); margin: 0 0 10px; }
.site-foot ul { display: grid; gap: 8px; }
.site-foot a { color: var(--ink-2); text-decoration: none; }
.site-foot a:hover { color: var(--accent); text-decoration: underline; }
.site-foot a[aria-current="page"] { color: var(--ink); font-weight: 600; }
.site-foot .legal { font-size: 0.75rem; color: var(--muted); max-width: 110ch; border-top: 1px solid var(--line); padding-top: 20px; }
.site-foot .legal a { color: inherit; text-decoration: underline; }

.switch { display: inline-flex; align-items: center; gap: 10px; border: 0; background: none; cursor: pointer; padding: 6px 0;
  font: inherit; font-size: 0.875rem; color: var(--ink-2); white-space: nowrap; }
.switch .track { width: 36px; height: 22px; border-radius: 999px; background: var(--line-2); position: relative; flex: none; transition: background 160ms; }
.switch .track::after { content: ""; position: absolute; top: 3px; left: 3px; width: 16px; height: 16px; border-radius: 50%;
  background: var(--surface); box-shadow: 0 1px 2px rgb(0 0 0 / 0.3); transition: transform 160ms; }
.switch[aria-checked="true"] .track { background: var(--accent); }
.switch[aria-checked="true"] .track::after { transform: translateX(14px); }
@media (prefers-reduced-motion: reduce) { .switch .track, .switch .track::after { transition: none; } }

@media (max-width: 760px) {
  .site-head-in { min-height: 52px; }
  .menu-btn { display: inline-flex; }
  .site-nav { position: static; }
  .site-nav ul { display: none; position: absolute; left: max(16px, env(safe-area-inset-left, 0px)); right: max(16px, env(safe-area-inset-right, 0px));
    top: calc(100% - 4px); flex-direction: column; align-items: stretch; gap: 2px; padding: 8px; background: var(--surface);
    border: 1px solid var(--line); border-radius: var(--r-lg); box-shadow: 0 6px 12px -4px rgb(0 0 0 / 0.22); z-index: 25; }
  .site-head { position: sticky; }
  .site-head-in { position: relative; }
  .site-nav.open ul { display: flex; }
  .site-nav a { display: flex; min-height: 44px; padding: 10px 12px; border-radius: var(--r); }
  .site-nav a[aria-current="page"] { box-shadow: inset 3px 0 0 var(--accent); background: var(--accent-soft); }
  .jump { display: inline-flex; }
  .page-nav ul { display: none; position: absolute; left: 0; right: 0; top: calc(100% + 4px); flex-direction: column; align-items: stretch;
    gap: 2px; padding: 8px; background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-lg);
    box-shadow: 0 6px 12px -4px rgb(0 0 0 / 0.22); z-index: 25; max-height: 60vh; overflow: auto; }
  .page-nav.open ul { display: flex; }
  .page-nav a { display: flex; min-height: 44px; border-radius: var(--r); padding: 10px 12px; font-size: 0.9375rem; }
  .page-tools .switch { font-size: 0.8125rem; }
  .foot-grid { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
  .foot-about { grid-column: 1 / -1; }
}
@media (max-width: 420px) { .sw-x { display: none; } }
@media (prefers-reduced-motion: reduce) { .jump svg { transition: none; } }
""" + provenance.CSS

JS = r"""
(() => {
  "use strict";
  // ---------- Site layout behaviour (gateway/site.py) ----------
  const head = document.getElementById("site-head");
  if (!head) return;
  const root = document.documentElement;
  const setHeadH = () => root.style.setProperty("--head-h", head.offsetHeight + "px");
  setHeadH();
  if (window.ResizeObserver) new ResizeObserver(setHeadH).observe(head); else window.addEventListener("resize", setHeadH);

  // Light or dark. The button names the theme it switches to; the choice is remembered.
  const themeBtn = document.getElementById("theme-toggle");
  if (themeBtn) {
    const dark = () => root.getAttribute("data-theme") === "dark" ||
      (!root.hasAttribute("data-theme") && window.matchMedia("(prefers-color-scheme: dark)").matches);
    const label = () => {
      const t = dark() ? "Switch to light mode" : "Switch to dark mode";
      themeBtn.setAttribute("aria-label", t); themeBtn.title = t;
    };
    themeBtn.addEventListener("click", () => {
      const next = dark() ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem("gw-theme", next); } catch (e) { /* storage unavailable: still switches */ }
      label();
    });
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", label);
    label();
  }

  // Disclosure menus: the site Menu and "Jump to". One open at a time; Escape, outside clicks
  // and choosing a link close them.
  const menus = [...head.querySelectorAll(".site-nav, .page-nav")].map((nav) => ({ nav, btn: nav.querySelector("button") }));
  const close = (m, focus) => { m.nav.classList.remove("open"); m.btn.setAttribute("aria-expanded", "false"); if (focus) m.btn.focus(); };
  menus.forEach((m) => {
    m.btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const open = !m.nav.classList.contains("open");
      menus.forEach((o) => close(o));
      if (open) { m.nav.classList.add("open"); m.btn.setAttribute("aria-expanded", "true"); }
    });
    m.nav.addEventListener("click", (e) => { if (e.target.closest("a")) close(m); });
  });
  document.addEventListener("click", (e) => { menus.forEach((m) => { if (!m.nav.contains(e.target)) close(m); }); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") menus.forEach((m) => { if (m.nav.classList.contains("open")) close(m, true); }); });

  // "On this page": mark the section in view.
  const pageNav = document.getElementById("page-nav");
  if (!pageNav) return;
  const current = document.getElementById("jump-current");
  const links = [...pageNav.querySelectorAll('a[href^="#"]')];
  const mark = () => {
    const y = window.scrollY + head.offsetHeight + 40;
    let cur = null;
    links.forEach((a) => {
      if (a.hidden) return;
      const sec = document.getElementById(a.getAttribute("href").slice(1));
      // Skip sections that are not shown (hidden themselves or inside something hidden).
      if (sec && sec.getClientRects().length && sec.getBoundingClientRect().top + window.scrollY <= y) cur = a;
    });
    if (!cur) cur = links.find((a) => !a.hidden);
    links.forEach((a) => (a === cur ? a.setAttribute("aria-current", "location") : a.removeAttribute("aria-current")));
    if (cur && current) current.textContent = cur.textContent;
  };
  window.addEventListener("scroll", mark, { passive: true });
  window.addEventListener("resize", mark);
  window.gwMarkSection = mark;   // pages call this after showing or hiding a section
  mark();
})();
"""


def fill(page: str, current: str, sections: list[tuple[str, str, str | None]] = (), tools: str = "",
         about: str = "") -> str:
    """Put the shared layout into a page that carries the four placeholders."""
    for marker in ("__SITE_CSS__", "__SITE_HEADER__", "__SITE_FOOTER__", "__SITE_JS__"):
        if page.count(marker) != 1:
            raise ValueError(f"page must contain {marker} exactly once")
    return (page.replace("__SITE_CSS__", CSS)
                .replace("__SITE_HEADER__", header(current, sections, tools))
                .replace("__SITE_FOOTER__", footer(current, about))
                .replace("__SITE_JS__", JS))
