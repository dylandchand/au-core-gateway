# The gateway with the official HL7 validator and the pinned FHIR packages baked in.
#
#   docker compose up --build      runs the demo: report and dashboard in ./out
#   docker compose run --rm gateway run <files or folders> --out out --dashboard
#
# Restricted networks only (packages.fhir.org blocked): build with
#   --build-arg PACKAGES=offline
# which uses the same stand-in packages as `make setup-offline`. Reports from such an image
# say so (package_substitutions), and their numbers must not be published.
FROM eclipse-temurin:21-jre-noble

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-yaml \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY versions.toml pyproject.toml ./
COPY scripts/ scripts/
ARG PACKAGES=official
RUN python3 scripts/fetch_validator.py \
 && if [ "$PACKAGES" = "offline" ]; then python3 scripts/bootstrap_offline.py; \
    else python3 scripts/fetch_packages.py \
      && if [ -s /root/.fhir/packages/SUBSTITUTIONS.txt ]; then \
           echo "Official FHIR packages could not all be downloaded; stand-ins were used:"; \
           cat /root/.fhir/packages/SUBSTITUTIONS.txt; exit 1; fi; fi

COPY gateway/ gateway/
COPY fixtures/ fixtures/
# Warm-up run on the golden fixtures: proves the validator and packages work inside the
# image, and fails the build if anything golden stops conforming.
RUN python3 -m gateway run fixtures/golden --out /tmp/warmup --no-store > /tmp/warmup.log \
 && grep -q "failed: 0" /tmp/warmup.log \
 || (cat /tmp/warmup.log; exit 1)

ENV GATEWAY_VALIDATOR_XMX=3g
ENTRYPOINT ["python3", "-m", "gateway"]
CMD ["run", "fixtures/golden", "fixtures/faults", "--out", "out", "--dashboard"]
