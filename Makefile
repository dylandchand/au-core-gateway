PY ?= python3
export GATEWAY_VALIDATOR_XMX ?= 3g

.PHONY: help setup setup-offline fixtures test validate gate demo clean

help:
	@echo "make setup          install Python deps, download the HL7 validator and pinned FHIR packages"
	@echo "make setup-offline  setup for networks that block packages.fhir.org (restricted sandboxes only)"
	@echo "make test           unit tests (fast, no Java)"
	@echo "make validate       integration tests against the real HL7 validator (~1 min)"
	@echo "make gate           everything CI runs: fixtures reproducible + unit + validator tests"
	@echo "make demo           run the gateway on all fixtures and print the report"

setup:
	$(PY) -m pip install -q -e ".[dev]"
	$(PY) scripts/fetch_validator.py
	$(PY) scripts/fetch_packages.py

setup-offline: setup
	$(PY) scripts/bootstrap_offline.py

fixtures:
	$(PY) scripts/make_fixtures.py

test:
	$(PY) -m pytest -q

validate:
	$(PY) -m pytest -q -m validator

gate: fixtures
	git diff --exit-code -- fixtures/ || (echo "Fixtures are not reproducible from scripts/make_fixtures.py" && exit 1)
	$(MAKE) test validate

demo:
	$(PY) -m gateway run fixtures/golden fixtures/faults --source demo-lab --out out

clean:
	rm -rf out .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
