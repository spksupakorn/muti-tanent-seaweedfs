#!/bin/sh
# One command: bring the stack up, then run the in-network tests and the host-side port checks.
set -eu
cd "$(dirname "$0")/.."
docker compose up -d --build --wait
docker compose --profile test run --rm --build tests
uv run --no-project --with pytest pytest -q -p no:cacheprovider tests/host
