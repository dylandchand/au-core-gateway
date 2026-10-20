"""Package download: official registries are retried before anything else is tried."""

import urllib.error

import pytest

from tests.fault_feed import load_script

fetch = load_script("fetch_packages")


def fake_get(responses, calls):
    def _get(url):
        calls.append(url)
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    return _get


def test_a_transient_error_is_retried_on_the_same_registry(monkeypatch):
    calls = []
    monkeypatch.setattr(fetch.time, "sleep", lambda s: None)
    monkeypatch.setattr(fetch, "_get", fake_get([urllib.error.URLError("reset"), b"pkg"], calls))
    assert fetch.from_fhir_registry("hl7.fhir.r4.core", "4.0.1") == b"pkg"
    assert calls == ["https://packages.fhir.org/hl7.fhir.r4.core/4.0.1"] * 2


def test_not_found_moves_to_the_secondary_registry(monkeypatch):
    calls = []
    monkeypatch.setattr(fetch.time, "sleep", lambda s: None)
    not_found = urllib.error.HTTPError("u", 404, "Not Found", None, None)
    monkeypatch.setattr(fetch, "_get", fake_get([not_found, b"pkg"], calls))
    assert fetch.from_fhir_registry("x", "1.0.0") == b"pkg"
    assert calls == ["https://packages.fhir.org/x/1.0.0", "https://packages2.fhir.org/packages/x/1.0.0"]


def test_gives_up_after_both_registries(monkeypatch):
    monkeypatch.setattr(fetch.time, "sleep", lambda s: None)
    calls = []
    monkeypatch.setattr(fetch, "_get", fake_get([urllib.error.URLError("down")] * 6, calls))
    with pytest.raises(urllib.error.URLError):
        fetch.from_fhir_registry("x", "1.0.0")
    assert len(calls) == 2 * fetch.ATTEMPTS
