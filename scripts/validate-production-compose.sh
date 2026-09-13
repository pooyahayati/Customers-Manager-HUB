#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  echo "Usage: $0 [environment-file]" >&2
}

die() {
  echo "Production Compose validation failed: $*" >&2
  exit 1
}

if [ "$#" -gt 1 ]; then
  usage
  exit 2
fi

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_dir=$(cd -- "${script_dir}/.." && pwd)
env_file=${1:-"${project_dir}/.env.production"}

command -v docker >/dev/null 2>&1 || die "docker is required"
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required"
[ -f "$env_file" ] || die "environment file not found: ${env_file}"

config_file=$(mktemp)
trap 'rm -f -- "$config_file"' EXIT

docker compose \
  --env-file "$env_file" \
  -f "${project_dir}/compose.yaml" \
  -f "${project_dir}/compose.production.yaml" \
  config >"$config_file"

service_block() {
  local service=$1
  awk -v service="$service" '
    $0 == "  " service ":" { inside = 1; print; next }
    inside && /^  [[:alnum:]_.-]+:$/ { exit }
    inside { print }
  ' "$config_file"
}

while IFS= read -r service; do
  [ "$service" = "caddy" ] && continue
  if service_block "$service" | grep -Eq '^    ports:$'; then
    die "service '${service}' publishes a host port; only caddy may publish ports"
  fi
done < <(
  docker compose \
    --env-file "$env_file" \
    -f "${project_dir}/compose.yaml" \
    -f "${project_dir}/compose.production.yaml" \
    config --services
)

caddy_block=$(service_block caddy)
printf '%s\n' "$caddy_block" | grep -Eq '^    ports:$' || die "caddy publishes no ports"

published_80=0
published_443=0
while IFS= read -r port; do
  case "$port" in
    80) published_80=$((published_80 + 1)) ;;
    443) published_443=$((published_443 + 1)) ;;
    *) die "caddy publishes unexpected host port ${port}" ;;
  esac
done < <(
  printf '%s\n' "$caddy_block" |
    awk '/^[[:space:]]+published:/ { gsub(/"/, "", $2); print $2 }'
)

[ "$published_80" -eq 1 ] || die "caddy must publish TCP port 80 exactly once"
[ "$published_443" -eq 2 ] || die "caddy must publish TCP and UDP port 443"

printf '%s\n' "$caddy_block" | grep -A3 -E 'target: 80$' | grep -q 'protocol: tcp' ||
  die "caddy TCP port 80 mapping is missing"
printf '%s\n' "$caddy_block" | grep -A3 -E 'target: 443$' | grep -q 'protocol: tcp' ||
  die "caddy TCP port 443 mapping is missing"
printf '%s\n' "$caddy_block" | grep -A3 -E 'target: 443$' | grep -q 'protocol: udp' ||
  die "caddy UDP port 443 mapping is missing"

echo "Production Compose validation: PASS"
