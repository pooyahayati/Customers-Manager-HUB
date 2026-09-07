#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <api-base-url> <web-base-url>" >&2
  exit 2
fi

api_url="${1%/}"
web_url="${2%/}"
workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT

curl_common=(--fail --silent --show-error --max-time 15)

echo "Checking API health..."
curl "${curl_common[@]}" "${api_url}/health" >/dev/null

echo "Checking API readiness..."
curl "${curl_common[@]}" "${api_url}/ready" >/dev/null

echo "Checking Admin Console..."
curl "${curl_common[@]}" "${web_url}/" >/dev/null

if [ -n "${SMOKE_EMAIL:-}" ] || [ -n "${SMOKE_PASSWORD:-}" ]; then
  if [ -z "${SMOKE_EMAIL:-}" ] || [ -z "${SMOKE_PASSWORD:-}" ]; then
    echo "SMOKE_EMAIL and SMOKE_PASSWORD must be provided together" >&2
    exit 2
  fi

  login_payload=$(python - <<'PY'
import json
import os

print(json.dumps({"email": os.environ["SMOKE_EMAIL"], "password": os.environ["SMOKE_PASSWORD"]}))
PY
  )

  echo "Checking authenticated operator session..."
  curl "${curl_common[@]}" \
    -H "Content-Type: application/json" \
    -c "${workdir}/cookies.txt" \
    --data "${login_payload}" \
    "${api_url}/api/v1/auth/login" >/dev/null

  curl "${curl_common[@]}" \
    -b "${workdir}/cookies.txt" \
    "${api_url}/api/v1/auth/me" >/dev/null

  curl "${curl_common[@]}" \
    -b "${workdir}/cookies.txt" \
    "${api_url}/api/v1/tenants" > "${workdir}/tenants.json"

  python - "${workdir}/tenants.json" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
if not isinstance(payload, list) or not payload:
    raise SystemExit("Authenticated smoke user has no active tenant membership")
PY
elif [ "${SMOKE_REQUIRE_AUTH:-0}" = "1" ]; then
  echo "Authenticated smoke is required but SMOKE_EMAIL/SMOKE_PASSWORD are missing" >&2
  exit 2
else
  echo "Authenticated smoke skipped; set SMOKE_EMAIL and SMOKE_PASSWORD to enable it."
fi

echo "Deployment smoke: PASS"
