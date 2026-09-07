#!/usr/bin/env bash
set -euo pipefail

MODE="${1:---local}"
VALIDATION_DB="cmh_mvp_validation"
VALIDATION_REDIS_DB="15"
COMPOSE=(docker compose -f compose.yaml)

print_evidence() {
  cat <<'EOF'
Commercial MVP capability evidence
  01 Docker fresh start / readiness        -> CI Docker and Compose gate
  02 Tenant creation                       -> tenant auth integration
  03 Users and roles                       -> tenant auth integration
  04 OpenAI/Gemini credential boundary     -> config/provider + Security gates
  05 AI task model routing                 -> AI gateway / agent integration
  06 Telegram channel                      -> Telegram channel integration
  07 Website Chat                          -> Website Chat integration
  08 Text and voice capabilities           -> Telegram / Website / voice integration
  09 PDF/XLSX knowledge                    -> knowledge integration
  10 Live business API tool                -> tool integration
  11 Published agent and prompt            -> agent/prompt integration
  12 Text and voice inbound                -> channel / voice integration
  13 RAG and live tool execution           -> knowledge / tool integration
  14 Originating-channel response          -> agent / tool / knowledge / voice integration
  15 Human escalation                      -> handoff / policy integration
  16 Contacts, conversations and memory    -> conversation / memory integration
  17 Trace, audit, usage and analytics     -> AI/tool/knowledge/analytics integration
EOF
}

run_integration_regression() {
  test "${RUN_DB_INTEGRATION:-}" = "1"
  print_evidence
  uv run pytest apps/api/tests/test_*_integration.py
  printf '%s\n' 'Commercial MVP integration capability matrix: PASS'
}

cleanup_local() {
  set +e
  "${COMPOSE[@]}" exec -T redis redis-cli -n "$VALIDATION_REDIS_DB" FLUSHDB >/dev/null 2>&1
  if [ -n "${MVP_PG_USER:-}" ]; then
    "${COMPOSE[@]}" exec -T postgres dropdb -U "$MVP_PG_USER" --if-exists "$VALIDATION_DB" >/dev/null 2>&1
  fi
}

case "$MODE" in
  --ci)
    run_integration_regression
    ;;
  --local)
    command -v docker >/dev/null
    command -v uv >/dev/null
    "${COMPOSE[@]}" config --quiet
    "${COMPOSE[@]}" up -d postgres redis

    MVP_PG_USER="$("${COMPOSE[@]}" exec -T postgres sh -c 'printf %s "$POSTGRES_USER"')"
    MVP_PG_PASSWORD="$("${COMPOSE[@]}" exec -T postgres sh -c 'printf %s "$POSTGRES_PASSWORD"')"
    MVP_PG_PORT="$("${COMPOSE[@]}" port postgres 5432 | tail -n 1 | awk -F: '{print $NF}')"
    MVP_REDIS_PORT="$("${COMPOSE[@]}" port redis 6379 | tail -n 1 | awk -F: '{print $NF}')"
    export MVP_PG_USER MVP_PG_PASSWORD

    trap cleanup_local EXIT

    "${COMPOSE[@]}" exec -T postgres dropdb -U "$MVP_PG_USER" --if-exists "$VALIDATION_DB"
    "${COMPOSE[@]}" exec -T postgres createdb -U "$MVP_PG_USER" "$VALIDATION_DB"
    "${COMPOSE[@]}" exec -T redis redis-cli -n "$VALIDATION_REDIS_DB" FLUSHDB >/dev/null

    encoded_user="$(python3 -c 'import os, urllib.parse; print(urllib.parse.quote(os.environ["MVP_PG_USER"], safe=""))')"
    encoded_password="$(python3 -c 'import os, urllib.parse; print(urllib.parse.quote(os.environ["MVP_PG_PASSWORD"], safe=""))')"

    export APP_ENV=test
    export DATABASE_URL="postgresql+psycopg://${encoded_user}:${encoded_password}@127.0.0.1:${MVP_PG_PORT}/${VALIDATION_DB}"
    export REDIS_URL="redis://127.0.0.1:${MVP_REDIS_PORT}/${VALIDATION_REDIS_DB}"
    export RUN_DB_INTEGRATION=1
    export ENCRYPTION_KEY="dmFsaWRfdGVzdF9rZXlfMzJfYnl0ZXNfbG9uZ19fX18="
    export TELEGRAM_WEBHOOK_BASE_URL="https://mvp-validation.example.test"
    export RATE_LIMIT_LOGIN_REQUESTS=10000
    export RATE_LIMIT_PUBLIC_REQUESTS=100000
    export RATE_LIMIT_WEBHOOK_REQUESTS=100000

    uv sync --frozen
    uv run alembic upgrade head
    uv run alembic check
    run_integration_regression
    ;;
  *)
    printf 'Usage: %s [--local|--ci]\n' "$0" >&2
    exit 2
    ;;
esac
