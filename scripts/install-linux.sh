#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_dir=$(cd -- "${script_dir}/.." && pwd)
env_file="${project_dir}/.env.production"
state_file=""
domain=""
acme_email=""
tenant_name=""
tenant_slug=""
owner_email=""
env_created=0
compose_ready=0
cookie_file=""
pending_temp=""
compose_cmd=()

usage() {
  cat <<'EOF'
Usage:
  bash scripts/install-linux.sh \
    --domain hub.example.com \
    --email ops@example.com \
    --tenant-name "Example Business" \
    --tenant-slug example-business \
    --owner-email owner@example.com \
    [--env-file /secure/path/cmh.env]

On a rerun, omit identity options and reuse the existing environment file.
The installer never installs Docker and never removes Docker volumes.
EOF
}

die() {
  echo "Install failed: $*" >&2
  exit 1
}

cleanup() {
  if [ -n "$cookie_file" ]; then
    rm -f -- "$cookie_file"
  fi
  if [ -n "$pending_temp" ]; then
    rm -f -- "$pending_temp"
  fi
}

on_error() {
  local status=$?
  echo "Install stopped safely at line ${BASH_LINENO[0]}; existing environment and volumes were preserved." >&2
  if [ "$compose_ready" -eq 1 ]; then
    "${compose_cmd[@]}" ps >&2 || true
  fi
  exit "$status"
}

trap cleanup EXIT
trap on_error ERR

while [ "$#" -gt 0 ]; do
  case "$1" in
    --domain)
      [ "$#" -ge 2 ] || die "--domain requires a value"
      domain=$2
      shift 2
      ;;
    --email)
      [ "$#" -ge 2 ] || die "--email requires a value"
      acme_email=$2
      shift 2
      ;;
    --tenant-name)
      [ "$#" -ge 2 ] || die "--tenant-name requires a value"
      tenant_name=$2
      shift 2
      ;;
    --tenant-slug)
      [ "$#" -ge 2 ] || die "--tenant-slug requires a value"
      tenant_slug=$2
      shift 2
      ;;
    --owner-email)
      [ "$#" -ge 2 ] || die "--owner-email requires a value"
      owner_email=$2
      shift 2
      ;;
    --env-file)
      [ "$#" -ge 2 ] || die "--env-file requires a value"
      env_file=$2
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage
      die "unknown option: $1"
      ;;
  esac
done

state_file="${env_file}.install-state"

for required_command in awk chmod curl flock getent git grep mktemp mv openssl sed stat tr wc; do
  command -v "$required_command" >/dev/null 2>&1 ||
    die "required command is missing: ${required_command}"
done
command -v docker >/dev/null 2>&1 || die "Docker Engine is required but was not found"
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required"
docker info >/dev/null 2>&1 ||
  die "the current user cannot reach a running Docker Engine"

compose_version=$(docker compose version --short | sed 's/^v//')
minimum_compose_version=2.24.4
version_is_supported=$(awk -v actual="$compose_version" -v minimum="$minimum_compose_version" '
  BEGIN {
    split(actual, a, "."); split(minimum, m, ".")
    for (i = 1; i <= 3; i++) {
      a[i] += 0; m[i] += 0
      if (a[i] > m[i]) { print "yes"; exit }
      if (a[i] < m[i]) { print "no"; exit }
    }
    print "yes"
  }
')
[ "$version_is_supported" = "yes" ] ||
  die "Docker Compose ${minimum_compose_version} or newer is required for secure port resets"

exec 9>"/tmp/customers-manager-hub-production-install.lock"
flock -n 9 || die "another production installation is already running"

cd -- "$project_dir"
source_revision=$(git rev-parse --verify HEAD)
if [ -n "$(git status --porcelain --untracked-files=normal)" ]; then
  die "production installation requires a clean, reviewed Git checkout"
fi
if [ -f "$state_file" ]; then
  installed_revision=$(sed -n '1p' "$state_file")
  [ "$installed_revision" = "$source_revision" ] ||
    die "source revision changed; follow docs/operations/PRODUCTION-INSTALL-LINUX.md for a backed-up upgrade"
fi

validate_plain_env_value() {
  local label=$1
  local value=$2
  [ -n "$value" ] || die "${label} must not be empty"
  case "$value" in
    *$'\n'*|*$'\r'*|*'#'*|*'$'*|*'"'*)
      die "${label} contains characters that are unsafe in a dotenv file"
      ;;
  esac
  [[ "$value" != *\\* ]] ||
    die "${label} contains characters that are unsafe in a dotenv file"
}

if [ ! -e "$env_file" ]; then
  [ -n "$domain" ] || die "--domain is required for the first installation"
  [ -n "$acme_email" ] || die "--email is required for the first installation"
  [ -n "$tenant_name" ] || die "--tenant-name is required for the first installation"
  [ -n "$tenant_slug" ] || die "--tenant-slug is required for the first installation"
  [ -n "$owner_email" ] || die "--owner-email is required for the first installation"

  [[ "$domain" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}$ ]] ||
    die "--domain must be a public DNS hostname without a scheme or path"
  [[ "$acme_email" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]] ||
    die "--email must be a valid ACME contact address"
  [[ "$owner_email" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]] ||
    die "--owner-email must be a valid email address"
  [[ "$tenant_slug" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]] ||
    die "--tenant-slug must contain lowercase letters, digits, and single hyphens"
  [ "${#tenant_name}" -le 200 ] || die "--tenant-name must not exceed 200 characters"
  validate_plain_env_value "ACME email" "$acme_email"
  validate_plain_env_value "Owner email" "$owner_email"
  validate_plain_env_value "tenant name" "$tenant_name"

  env_parent=$(cd -- "$(dirname -- "$env_file")" && pwd)
  env_file="${env_parent}/$(basename -- "$env_file")"
  state_file="${env_file}.install-state"
  pending_temp=$(mktemp "${env_file}.tmp.XXXXXX")
  postgres_password=$(openssl rand -hex 24)
  encryption_key=$(openssl rand -base64 32 | tr '/+' '_-' | tr -d '\n')
  s3_access_key=$(openssl rand -hex 12)
  s3_secret_key=$(openssl rand -hex 32)
  owner_password=$(openssl rand -hex 18)

  {
    printf '%s\n' '# Generated by scripts/install-linux.sh. Keep mode 0600 and never commit.'
    printf 'CMH_DOMAIN=%s\n' "$domain"
    printf 'ACME_EMAIL=%s\n' "$acme_email"
    printf '%s\n' 'COMPOSE_PROJECT_NAME=cmh-production'
    printf '%s\n' 'CMH_DOCKER_SUBNET=172.30.0.0/24'
    printf '%s\n' 'CADDY_INTERNAL_IP=172.30.0.10'
    printf '%s\n' 'TRUSTED_PROXY_CIDRS=172.30.0.10/32'
    printf '%s\n' 'CADDY_IMAGE=caddy:2.10.2-alpine'
    printf '%s\n' 'CMH_POSTGRES_IMAGE=customers-manager-hub-production/postgres:18.6-pgvector0.8.6'
    printf '%s\n' 'APP_ENV=production'
    printf '%s\n' 'APP_NAME=Customers Manager HUB'
    printf '%s\n' 'APP_DEBUG=false'
    printf '%s\n' 'APP_LOG_LEVEL=INFO'
    printf '%s\n' 'RATE_LIMIT_ENABLED=true'
    printf '%s\n' 'RATE_LIMIT_WINDOW_SECONDS=60'
    printf '%s\n' 'RATE_LIMIT_LOGIN_REQUESTS=10'
    printf '%s\n' 'RATE_LIMIT_PUBLIC_REQUESTS=120'
    printf '%s\n' 'RATE_LIMIT_WEBHOOK_REQUESTS=180'
    printf '%s\n' 'WORKER_MAX_DELIVERY_ATTEMPTS=5'
    printf 'TELEGRAM_WEBHOOK_BASE_URL=https://%s\n' "$domain"
    printf 'ENCRYPTION_KEY=%s\n' "$encryption_key"
    printf '%s\n' 'POSTGRES_DB=customers_manager_hub'
    printf '%s\n' 'POSTGRES_USER=cmh'
    printf 'POSTGRES_PASSWORD=%s\n' "$postgres_password"
    printf 'DATABASE_URL=postgresql+psycopg://cmh:%s@postgres:5432/customers_manager_hub\n' "$postgres_password"
    printf '%s\n' 'REDIS_URL=redis://redis:6379/0'
    printf '%s\n' 'S3_ENDPOINT_URL=http://object-storage:8333'
    printf '%s\n' 'S3_REGION=us-east-1'
    printf 'S3_ACCESS_KEY_ID=%s\n' "$s3_access_key"
    printf 'S3_SECRET_ACCESS_KEY=%s\n' "$s3_secret_key"
    printf '%s\n' 'S3_BUCKET=cmh-knowledge'
    printf '%s\n' 'S3_FORCE_PATH_STYLE=true'
    printf '%s\n' 'S3_ALLOW_INSECURE_INTERNAL_ENDPOINT=true'
    printf '%s\n' 'OPENAI_API_KEY='
    printf '%s\n' 'GOOGLE_GEMINI_API_KEY='
    printf 'BOOTSTRAP_TENANT_NAME=%s\n' "$tenant_name"
    printf 'BOOTSTRAP_TENANT_SLUG=%s\n' "$tenant_slug"
    printf 'BOOTSTRAP_OWNER_EMAIL=%s\n' "$owner_email"
    printf 'BOOTSTRAP_OWNER_PASSWORD=%s\n' "$owner_password"
  } >"$pending_temp"
  chmod 600 "$pending_temp"
  mv -- "$pending_temp" "$env_file"
  pending_temp=""
  env_created=1
  echo "Created protected production environment: ${env_file}"
else
  if [ -n "$domain$acme_email$tenant_name$tenant_slug$owner_email" ]; then
    die "identity options cannot be used when the environment file already exists; it was not overwritten"
  fi
  echo "Reusing existing production environment without modification: ${env_file}"
fi

read_env() {
  local key=$1
  awk -v key="$key" '
    index($0, key "=") == 1 {
      count++
      value = substr($0, length(key) + 2)
      sub(/\r$/, "", value)
    }
    END {
      if (count != 1) exit 2
      print value
    }
  ' "$env_file" || die "${key} must occur exactly once in ${env_file}"
}

mode=$(stat -c '%a' "$env_file")
if (( (8#$mode & 077) != 0 )); then
  die "${env_file} permissions are ${mode}; require 0600 or stricter"
fi

project_name=$(read_env COMPOSE_PROJECT_NAME)
domain=$(read_env CMH_DOMAIN)
acme_email=$(read_env ACME_EMAIL)
docker_subnet=$(read_env CMH_DOCKER_SUBNET)
caddy_internal_ip=$(read_env CADDY_INTERNAL_IP)
trusted_proxy_cidrs=$(read_env TRUSTED_PROXY_CIDRS)
postgres_db=$(read_env POSTGRES_DB)
postgres_user=$(read_env POSTGRES_USER)
postgres_password=$(read_env POSTGRES_PASSWORD)
database_url=$(read_env DATABASE_URL)
redis_url=$(read_env REDIS_URL)
encryption_key=$(read_env ENCRYPTION_KEY)
s3_endpoint_url=$(read_env S3_ENDPOINT_URL)
s3_access_key=$(read_env S3_ACCESS_KEY_ID)
s3_secret_key=$(read_env S3_SECRET_ACCESS_KEY)
s3_bucket=$(read_env S3_BUCKET)
s3_insecure_internal=$(read_env S3_ALLOW_INSECURE_INTERNAL_ENDPOINT)
telegram_webhook_base_url=$(read_env TELEGRAM_WEBHOOK_BASE_URL)
tenant_name=$(read_env BOOTSTRAP_TENANT_NAME)
tenant_slug=$(read_env BOOTSTRAP_TENANT_SLUG)
owner_email=$(read_env BOOTSTRAP_OWNER_EMAIL)
owner_password=$(read_env BOOTSTRAP_OWNER_PASSWORD)

for required_value in \
  project_name domain acme_email docker_subnet caddy_internal_ip trusted_proxy_cidrs \
  postgres_db postgres_user postgres_password database_url redis_url encryption_key \
  s3_endpoint_url s3_access_key s3_secret_key s3_bucket telegram_webhook_base_url \
  tenant_name tenant_slug owner_email owner_password; do
  [ -n "${!required_value}" ] || die "${required_value} is empty in ${env_file}"
done

[[ "$project_name" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || die "invalid COMPOSE_PROJECT_NAME"
[[ "$domain" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}$ ]] ||
  die "invalid CMH_DOMAIN"
[[ "$acme_email" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]] ||
  die "invalid ACME_EMAIL"
[[ "$docker_subnet" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}/[0-9]{1,2}$ ]] ||
  die "invalid CMH_DOCKER_SUBNET"
[[ "$owner_email" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]] ||
  die "invalid BOOTSTRAP_OWNER_EMAIL"
[[ "$tenant_slug" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]] || die "invalid BOOTSTRAP_TENANT_SLUG"
validate_plain_env_value "ACME email" "$acme_email"
validate_plain_env_value "Owner email" "$owner_email"
validate_plain_env_value "tenant name" "$tenant_name"
[[ "$s3_bucket" =~ ^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$ ]] || die "invalid S3_BUCKET"
[ "$trusted_proxy_cidrs" = "${caddy_internal_ip}/32" ] ||
  die "TRUSTED_PROXY_CIDRS must trust only CADDY_INTERNAL_IP"
[ "$database_url" = "postgresql+psycopg://${postgres_user}:${postgres_password}@postgres:5432/${postgres_db}" ] ||
  die "DATABASE_URL must match the private PostgreSQL service and generated credentials"
[ "$redis_url" = "redis://redis:6379/0" ] || die "REDIS_URL must use the private Redis service"
[ "$s3_endpoint_url" = "http://object-storage:8333" ] ||
  die "S3_ENDPOINT_URL must use the private single-host object-storage service"
[ "$s3_insecure_internal" = "true" ] ||
  die "S3_ALLOW_INSECURE_INTERNAL_ENDPOINT must be true for the private reference storage"
[ "$telegram_webhook_base_url" = "https://${domain}" ] ||
  die "TELEGRAM_WEBHOOK_BASE_URL must match the public HTTPS domain"
case "$postgres_password$s3_access_key$s3_secret_key" in
  *change-me*|*cmh-dev-*) die "development credentials are forbidden" ;;
esac
case "$encryption_key" in
  *[!A-Za-z0-9_=-]*) die "ENCRYPTION_KEY is not URL-safe base64" ;;
esac
if ! decoded_key_bytes=$(printf '%s' "$encryption_key" | tr '_-' '/+' | openssl base64 -d -A 2>/dev/null | wc -c); then
  die "ENCRYPTION_KEY is not valid base64"
fi
[ "${decoded_key_bytes//[[:space:]]/}" = "32" ] ||
  die "ENCRYPTION_KEY must decode to exactly 32 bytes"

getent ahosts "$domain" >/dev/null 2>&1 ||
  die "DNS for ${domain} does not resolve from this server"

compose_cmd=(
  docker compose
  --project-name "$project_name"
  --env-file "$env_file"
  -f "${project_dir}/compose.yaml"
  -f "${project_dir}/compose.production.yaml"
)
compose_ready=1

"${script_dir}/validate-production-compose.sh" "$env_file"

echo "Pulling pinned third-party runtime images..."
"${compose_cmd[@]}" pull caddy redis object-storage

echo "Building application and PostgreSQL images before changing runtime services..."
"${compose_cmd[@]}" build api worker web postgres

echo "Starting private infrastructure and waiting for health checks..."
"${compose_cmd[@]}" up -d --wait --wait-timeout 180 postgres redis object-storage

echo "Verifying S3 access and creating the configured bucket when absent..."
"${compose_cmd[@]}" run --rm -T --no-deps api python - <<'PY'
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from customers_manager_hub.config import get_settings

settings = get_settings()
client = boto3.client(
    "s3",
    endpoint_url=settings.s3_endpoint_url,
    region_name=settings.s3_region,
    aws_access_key_id=settings.s3_access_key_id.get_secret_value(),
    aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
    config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
)
try:
    client.head_bucket(Bucket=settings.s3_bucket)
except ClientError as exc:
    code = str(exc.response.get("Error", {}).get("Code", ""))
    if code not in {"404", "NoSuchBucket", "NotFound"}:
        raise
    client.create_bucket(Bucket=settings.s3_bucket)
PY

echo "Applying existing Alembic migrations and checking metadata drift..."
"${compose_cmd[@]}" run --rm -T api alembic upgrade head
"${compose_cmd[@]}" run --rm -T api alembic check

echo "Checking the initial Platform Owner state..."
owner_state=$(
  "${compose_cmd[@]}" run --rm -T --no-deps \
    -e "CMH_BOOTSTRAP_EMAIL=${owner_email}" api python - <<'PY'
import os

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from customers_manager_hub.config import get_settings
from customers_manager_hub.models import PlatformUser, Tenant

settings = get_settings()
engine = create_engine(settings.sqlalchemy_database_url)
with Session(engine) as db:
    identity_count = (db.scalar(select(func.count()).select_from(PlatformUser)) or 0) + (
        db.scalar(select(func.count()).select_from(Tenant)) or 0
    )
    matching_owner = db.scalar(
        select(PlatformUser.id).where(
            func.lower(PlatformUser.email) == os.environ["CMH_BOOTSTRAP_EMAIL"].lower(),
            PlatformUser.is_platform_owner.is_(True),
            PlatformUser.is_active.is_(True),
        )
    )
print("MATCH" if matching_owner is not None else "EMPTY" if identity_count == 0 else "CONFLICT")
engine.dispose()
PY
)
owner_state=${owner_state##*$'\n'}

case "$owner_state" in
  MATCH)
    echo "Requested active Platform Owner already exists; bootstrap skipped."
    ;;
  EMPTY)
    echo "Bootstrapping the initial tenant and Platform Owner through the application CLI..."
    bootstrap_output=""
    if ! bootstrap_output=$(
      printf '%s\n' "$owner_password" |
        "${compose_cmd[@]}" run --rm -T --no-deps api \
          python -m customers_manager_hub.bootstrap \
          --tenant-name "$tenant_name" \
          --tenant-slug "$tenant_slug" \
          --email "$owner_email" \
          --password-stdin 2>&1
    ); then
      printf '%s\n' "$bootstrap_output" >&2
      die "initial Platform Owner bootstrap failed"
    fi
    printf '%s\n' "$bootstrap_output"
    ;;
  CONFLICT)
    die "identity data exists but the configured active Platform Owner does not; manual recovery is required"
    ;;
  *)
    die "could not determine Platform Owner state"
    ;;
esac

echo "Starting the complete stack and waiting for container readiness..."
"${compose_cmd[@]}" up -d --wait --wait-timeout 300 api worker web caddy

for internal_service in api worker web postgres redis object-storage; do
  container_id=$("${compose_cmd[@]}" ps -q "$internal_service")
  [ -n "$container_id" ] || die "${internal_service} has no running container"
  [ -z "$(docker port "$container_id")" ] ||
    die "${internal_service} unexpectedly publishes a host port"
done
caddy_id=$("${compose_cmd[@]}" ps -q caddy)
[ -n "$(docker port "$caddy_id" 80/tcp)" ] || die "caddy does not publish TCP port 80"
[ -n "$(docker port "$caddy_id" 443/tcp)" ] || die "caddy does not publish TCP port 443"
[ -n "$(docker port "$caddy_id" 443/udp)" ] || die "caddy does not publish UDP port 443"

wait_for_url() {
  local label=$1
  local url=$2
  local attempt
  for ((attempt = 1; attempt <= 30; attempt++)); do
    if curl --fail --silent --show-error --max-time 5 "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 5
  done
  die "${label} did not become ready at ${url}"
}

echo "Waiting for public HTTPS and ACME certificate readiness..."
wait_for_url "API liveness" "https://${domain}/health"
wait_for_url "API dependency readiness" "https://${domain}/ready"
wait_for_url "Admin Console" "https://${domain}/"
redirect_target=$(curl --silent --show-error --max-time 10 --output /dev/null \
  --write-out '%{redirect_url}' "http://${domain}/")
case "$redirect_target" in
  "https://${domain}"/*) ;;
  *) die "public HTTP does not redirect to the configured HTTPS domain" ;;
esac

cookie_file=$(mktemp)
login_payload=$(printf '{"email":"%s","password":"%s"}' "$owner_email" "$owner_password")
printf '%s' "$login_payload" |
  curl --fail --silent --show-error --max-time 20 \
    -H 'Content-Type: application/json' \
    -c "$cookie_file" \
    --data-binary @- \
    "https://${domain}/api/v1/auth/login" >/dev/null
owner_response=$(curl --fail --silent --show-error --max-time 20 \
  -b "$cookie_file" "https://${domain}/api/v1/auth/me")
printf '%s' "$owner_response" | grep -Eq '"is_platform_owner"[[:space:]]*:[[:space:]]*true' ||
  die "authenticated bootstrap account is not a Platform Owner"

pending_temp=$(mktemp "${state_file}.tmp.XXXXXX")
printf '%s\n' "$source_revision" >"$pending_temp"
chmod 600 "$pending_temp"
mv -- "$pending_temp" "$state_file"
pending_temp=""

"${compose_cmd[@]}" ps
echo "Customers Manager HUB production installation: PASS"
echo "Admin Console: https://${domain}/"
echo "Platform Owner email: ${owner_email}"
if [ "$env_created" -eq 1 ]; then
  echo "Initial Platform Owner password: ${owner_password}"
  echo "The generated credentials remain in the protected environment file: ${env_file}"
fi
