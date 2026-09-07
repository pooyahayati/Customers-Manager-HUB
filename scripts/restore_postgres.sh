#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: $0 BACKUP.dump TARGET_DATABASE" >&2
  exit 64
fi

backup=$1
target_db=$2
primary_db=${POSTGRES_DB:-customers_manager_hub}
postgres_user=${POSTGRES_USER:-cmh}

if [ ! -s "$backup" ]; then
  echo "backup file does not exist or is empty: $backup" >&2
  exit 66
fi

if [[ ! "$target_db" =~ ^[A-Za-z_][A-Za-z0-9_]{0,62}$ ]]; then
  echo "invalid target database name" >&2
  exit 64
fi

if [ "$target_db" = "$primary_db" ] && [ "${ALLOW_IN_PLACE_RESTORE:-0}" != "1" ]; then
  echo "refusing in-place restore without ALLOW_IN_PLACE_RESTORE=1" >&2
  exit 77
fi

docker compose exec -T postgres dropdb -U "$postgres_user" --if-exists "$target_db"
docker compose exec -T postgres createdb -U "$postgres_user" "$target_db"

if ! docker compose exec -T postgres \
  pg_restore -U "$postgres_user" -d "$target_db" --no-owner --no-privileges <"$backup"; then
  docker compose exec -T postgres dropdb -U "$postgres_user" --if-exists "$target_db" || true
  exit 1
fi

version_count=$(docker compose exec -T postgres \
  psql -U "$postgres_user" -d "$target_db" -tAc "SELECT count(*) FROM alembic_version")
if [ "${version_count//[[:space:]]/}" != "1" ]; then
  echo "restored database does not contain exactly one alembic_version row" >&2
  exit 1
fi

echo "PostgreSQL restore validated in database $target_db" >&2
