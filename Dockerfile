# Runs the gateway with the official HL7 validator and pinned packages baked in.
#   docker compose run --rm gateway
FROM eclipse-temurin:21-jre-noble

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-yaml \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY versions.toml pyproject.toml ./
COPY scripts/ scripts/
RUN python3 scripts/fetch_validator.py && python3 scripts/fetch_packages.py

COPY gateway/ gateway/
COPY fixtures/ fixtures/
# Warm-up run: lets the validator download any remaining dependencies into the image,
# so later runs work offline.
RUN python3 -m gateway run fixtures/golden --out /tmp/warmup > /dev/null

ENV GATEWAY_VALIDATOR_XMX=3g
ENTRYPOINT ["python3", "-m", "gateway"]
CMD ["run", "fixtures/golden", "fixtures/faults", "--out", "out"]
