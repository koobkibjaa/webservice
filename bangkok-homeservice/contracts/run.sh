#!/bin/sh
# The contract tests, in order: the consumer tests write the pacts, then the providers are verified
# against them. Run with: docker compose run --rm contract-tests   (same command in CI)
set -e
mkdir -p "$PACT_DIR"
rm -f "$PACT_DIR"/*.json
echo "== Consumer tests (monolith): writing pacts to $PACT_DIR"
node --test contracts/consumer/*.pact.js
echo "== Provider verification against the running services"
node contracts/verify.js
