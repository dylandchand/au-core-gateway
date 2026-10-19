PY ?= python3
export GATEWAY_VALIDATOR_XMX ?= 3g

.PHONY: help setup setup-offline fixtures test validate scale population faults gate demo demo-scale demo-faults history clean

help:
	@echo "make setup          install Python deps, download the HL7 validator and pinned FHIR packages"
	@echo "make setup-offline  setup for networks that block packages.fhir.org (restricted sandboxes only)"
	@echo "make test           unit tests (fast, no Java)"
	@echo "make validate       integration tests against the real HL7 validator (~1 min)"
	@echo "make population     generate a seeded Synthea population as HL7 v2 (N=1000)"
	@echo "make scale          run the population end to end and check it against the source (N=1000)"
	@echo "make gate           fixtures reproducible + unit + validator tests (what every push runs)"
	@echo "make demo           run the gateway on all fixtures; report + dashboard in out/"
	@echo "make demo-scale     population + planted faults; dashboard in out/demo/ (N=1000)"
	@echo "make faults         break 25 messages per fault type in the population (data/synthea-N-faulted)"
	@echo "make demo-faults    run the faulted population, measure detection; dashboard in out/faulted/"
	@echo "make history        runs recorded in out/history.db (compare two: python -m gateway diff)"

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

N ?= 1000
population:
	$(PY) scripts/generate_population.py --patients $(N)

scale:
	GATEWAY_SCALE_PATIENTS=$(N) $(PY) -m pytest -q -m scale

gate: fixtures
	git diff --exit-code -- fixtures/ || (echo "Fixtures are not reproducible from scripts/make_fixtures.py" && exit 1)
	$(MAKE) test validate

demo:
	$(PY) -m gateway run fixtures/golden fixtures/faults --source demo-lab --out out --dashboard

demo-scale: population
	$(PY) -m gateway run data/synthea-$(N)/adt data/synthea-$(N)/oru fixtures/golden fixtures/faults --out out/demo --dashboard

PER_TYPE ?= 25
faults:
	@test -f data/synthea-$(N)/truth.json || $(MAKE) population
	$(PY) scripts/inject_faults.py data/synthea-$(N) data/synthea-$(N)-faulted --per-type $(PER_TYPE)

demo-faults: faults
	$(PY) -m gateway run data/synthea-$(N)-faulted --out out/faulted --dashboard --faults data/synthea-$(N)-faulted/faults.json

history:
	$(PY) -m gateway history

clean:
	rm -rf out .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
