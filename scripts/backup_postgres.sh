#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: $0 OUTPUT.dump" >&2
  exit 64
fi

output=$1
output_dir=$(dirname "$output")
mkdir -p "$output_dir"

tmp="${output}.tmp.$$"
cleanup() {
  rm -f "$tmp"
}
trap cleanup EXIT

postgres_user=${POSTGRES_USER:-cmh}
postgres_db=${POSTGRES_DB:-customers_manager_hub}

docker compose exec -T postgres \
  pg_dump -U "$postgres_user" -d "$postgres_db" -Fc >"$tmp"

test -s "$tmp"
chmod 600 "$tmp"
mv "$tmp" "$output"
trap - EXIT

echo "PostgreSQL backup written to $output" >&2
