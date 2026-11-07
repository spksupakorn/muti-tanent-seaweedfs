#!/bin/bash
# Real-browser check of the React app. Starts the stack, creates a user in two organizations,
# then drives Chromium through the file browser (tests/e2e/verify.mjs). Needs node and jq.
set -euo pipefail
cd "$(dirname "$0")/.."

docker compose up -d --build --wait
sfx=$RANDOM$RANDOM
export EMAIL="e2e-$sfx@example.com" PW="e2e-$sfx" TENANT_A="E2E A $sfx" TENANT_B="E2E B $sfx"
ops() { docker compose run --rm -T ops "$@" | tail -n 1; }
export TID_A=$(ops create_tenant "$TENANT_A")
ops create_tenant "$TENANT_B" >/dev/null
ops create_user "$EMAIL" "$PW" >/dev/null
ops add_membership "$EMAIL" "$TENANT_A" admin >/dev/null
ops add_membership "$EMAIL" "$TENANT_B" member >/dev/null
export TOKEN_A=$(curl -sf -X POST http://127.0.0.1:8000/api/auth/login -H 'content-type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PW\"}" | jq -r .access)

cd tests/e2e
npm ci --silent
npx playwright install chromium >/dev/null   # a no-op once the browser is cached
node verify.mjs
