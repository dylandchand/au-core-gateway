"""The shared site layout (gateway/site.py): every page has the same header, sections bar and
footer, a single h1 and main landmark, and every "on this page" link points at a real section."""

import re

import pytest

from gateway import site
from gateway.dashboard import build as dash
from gateway.inspector import build as insp


def test_current_page_is_marked_and_links_to_its_own_top():
    h = site.header("inspector")
    assert '<a href="#top" aria-current="page">Message Inspector</a>' in h
    assert f'<a href="{site.SITE_URL}">Results</a>' in h
    assert h.count('aria-current="page"') == 1


def test_links_that_leave_the_site_say_so():
    h = site.header("results")
    for label, href, where in site.ELSEWHERE:
        assert f'href="{href}"' in h and f"{label}<span aria-hidden=\"true\"> ↗</span><span class=\"sr\"> ({where})</span>" in h


def test_sections_bar_only_with_sections_and_optional_links_start_hidden():
    assert "page-nav" not in site.header("results")
    h = site.header("results", [("a", "A", None), ("b", "B", "nav-b")], site.TECH_SWITCH)
    assert '<a href="#a">A</a>' in h and '<a href="#b" id="nav-b" hidden>B</a>' in h
    assert 'id="tech-toggle"' in h


def test_fill_needs_each_placeholder_once():
    with pytest.raises(ValueError):
        site.fill("__SITE_CSS__ __SITE_HEADER__ __SITE_FOOTER__", "results")
    out = site.fill("__SITE_CSS__|__SITE_HEADER__|__SITE_FOOTER__|__SITE_JS__", "results", about="X.")
    assert "__SITE_" not in out and "site-head" in out and "site-foot" in out and "LOINC" in out


def _ids(html):
    return set(re.findall(r'\sid="([^"]+)"', html))


def _check_page(html, sections):
    assert html.count("<h1") == 1, "one h1 per page"
    assert html.count('<main ') == 1 and 'id="main"' in html
    assert html.count('class="site-head"') == 1 and html.count('class="site-foot"') == 1
    assert '<a class="skip" href="#main">' in html
    ids = _ids(html)
    for anchor, _, _ in sections:
        assert anchor in ids, f"section #{anchor} is missing from the page"


def test_dashboard_page_structure():
    html = dash.render({"meta": {}, "summary": {}})
    _check_page(html, dash.SECTIONS)
    assert html.index('class="site-head"') < html.index("<main ") < html.index('class="site-foot"')


def test_inspector_page_structure(tmp_path):
    html = insp.build(tmp_path / "site").read_text()
    _check_page(html, insp.SECTIONS)
    assert html.index('class="site-head"') < html.index("<main ") < html.index('class="site-foot"')


def test_monitor_page_structure(tmp_path):
    from gateway.monitor import build as mon
    html = mon.build(tmp_path / "site").read_text()
    _check_page(html, mon.SECTIONS)
    assert html.index('class="site-head"') < html.index("<main ") < html.index('class="site-foot"')


def test_theme_button_and_early_script():
    h = site.header("results")
    assert 'id="theme-toggle"' in h and 'aria-label="Switch to dark mode"' in h
    assert h.startswith("<script>") and "gw-theme" in h.split("</script>", 1)[0]
