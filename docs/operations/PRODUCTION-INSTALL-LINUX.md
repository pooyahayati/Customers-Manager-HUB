# Linux Production Installation

This runbook installs Customers Manager HUB on one Linux server that already has Docker Engine and
Docker Compose. It does not install or reconfigure Docker, DNS, a firewall, or a cloud account.

## 1. Scope and safety invariants

The production topology is:

```text
Internet -> Caddy :80/:443 -> API :8000 and Web :3000
                              |
                              +-> PostgreSQL / Redis / object storage (private network only)
```

Only Caddy has host port bindings. The production project uses separate named volumes and a
separate Compose project name; the development volumes from `compose.yaml` are not removed or
renamed. The installer never calls `docker compose down -v`, never overwrites an environment file,
and stops on the first failed prerequisite, health check, migration, bootstrap, or readiness check.

## 2. Prerequisites

- A supported Linux distribution with Docker Engine running.
- Docker Compose 2.24.4 or newer. The overlay uses the Compose `!reset` tag to remove development
  host ports.
- The current user can run `docker info` successfully. Use the host's documented rootless Docker or
  Docker group policy; the installer does not change permissions.
- `awk`, `curl`, `flock`, `getent`, `git`, `grep`, `mktemp`, `openssl`, `sed`, `seq`, `stat`, `tr`, and
  standard GNU core utilities.
- A clean, reviewed release checkout. The installer refuses a dirty Git working tree.
- A public DNS A/AAAA record for the chosen hostname pointing to this server.
- Inbound TCP 80 and TCP/UDP 443 allowed by the host firewall and upstream security group. Caddy
  needs TCP 80/443 for ACME and HTTPS; UDP 443 enables HTTP/3.
- No other process bound to host TCP 80 or TCP/UDP 443.

The default production subnet is `172.30.0.0/24`. If it conflicts with a host route or Docker
network, prepare a protected environment file from `.env.production.example` and change
`CMH_DOCKER_SUBNET`, `CADDY_INTERNAL_IP`, and `TRUSTED_PROXY_CIDRS` together. The trusted CIDR must
remain the Caddy address with `/32`.

## 3. First installation

From the repository root:

```bash
bash scripts/install-linux.sh \
  --domain hub.example.com \
  --email ops@example.com \
  --tenant-name "Example Business" \
  --tenant-slug example-business \
  --owner-email owner@example.com
```

The script performs these operations in order:

1. validates host tools, Docker access, Compose version, clean source state, DNS, and env safety;
2. creates `.env.production` with mode 0600 and generated PostgreSQL, encryption, S3, and Owner
   secrets when the file does not exist;
3. validates the merged Compose model and proves only Caddy publishes ports;
4. pulls pinned third-party images and builds application images before changing services;
5. starts and health-checks private infrastructure;
6. verifies S3 access and idempotently creates the configured bucket;
7. runs `alembic upgrade head` and `alembic check`;
8. calls the real `customers_manager_hub.bootstrap` CLI only for an empty identity database, or
   verifies the configured active Platform Owner on a rerun;
9. starts API, Worker, Web, and Caddy, then checks container and public HTTPS readiness;
10. authenticates the configured account and verifies `is_platform_owner=true`.

The initial Owner password is shown only after all checks pass and remains in the protected env
file. Protect that file as a production secret. The current application has no automated password
rotation command, so do not delete the value until a reviewed rotation path exists.

To use an environment file outside the checkout:

```bash
bash scripts/install-linux.sh --env-file /etc/customers-manager-hub/production.env \
  --domain hub.example.com \
  --email ops@example.com \
  --tenant-name "Example Business" \
  --tenant-slug example-business \
  --owner-email owner@example.com
```

The parent directory must already exist and be accessible only to the deployment operator.

## 4. Idempotent rerun and failure recovery

Rerun the same reviewed source revision without identity arguments:

```bash
bash scripts/install-linux.sh
```

or with the same external env path:

```bash
bash scripts/install-linux.sh --env-file /etc/customers-manager-hub/production.env
```

The env file is reused byte-for-byte. Existing volumes remain attached, migrations are idempotent,
the S3 bucket is retained, and an existing matching Platform Owner is not recreated.

On failure, inspect status and bounded logs without printing the env file:

```bash
docker compose --env-file .env.production \
  -f compose.yaml -f compose.production.yaml ps
docker compose --env-file .env.production \
  -f compose.yaml -f compose.production.yaml logs --tail=200 api worker web caddy
```

Correct the failed prerequisite and rerun. Do not use `down -v`; it deletes production data. A
conflicting non-empty identity database is an intentional hard stop and requires a reviewed manual
recovery.

## 5. Routine operations

Use both Compose files and the protected env file for every command:

```bash
docker compose --env-file .env.production \
  -f compose.yaml -f compose.production.yaml ps
docker compose --env-file .env.production \
  -f compose.yaml -f compose.production.yaml logs -f --tail=200
docker compose --env-file .env.production \
  -f compose.yaml -f compose.production.yaml restart api worker web caddy
```

Validate configuration without starting anything:

```bash
bash scripts/validate-production-compose.sh .env.production
```

Backups and incident procedures remain in
[`PRODUCTION_RUNBOOK.md`](PRODUCTION_RUNBOOK.md). Provider, Telegram, Website Chat, and authenticated
business-flow checks remain environment-specific release acceptance steps.

## 6. Backup-first upgrade runbook

There is intentionally no one-command updater. The installer records the successfully installed
Git revision in `<environment-file>.install-state` and refuses to apply a different revision.

For an upgrade:

1. Confirm CI, security, release provenance, migration review, and release notes for the exact target
   revision.
2. Create and verify a PostgreSQL backup with `scripts/backup_postgres.sh`. Snapshot Redis and
   object-storage volumes, and securely back up the env file and Caddy volumes.
3. Record the current image IDs, Git revision, and `docker compose ... ps` output for rollback.
4. Check out the exact target release without editing the protected env file.
5. Run `bash scripts/validate-production-compose.sh <environment-file>`.
6. Build or pull the target images before the maintenance window.
7. Stop external write traffic when the reviewed migration requires it, then run:

   ```bash
   docker compose --env-file .env.production \
     -f compose.yaml -f compose.production.yaml run --rm -T api alembic upgrade head
   docker compose --env-file .env.production \
     -f compose.yaml -f compose.production.yaml run --rm -T api alembic check
   docker compose --env-file .env.production \
     -f compose.yaml -f compose.production.yaml up -d --wait --wait-timeout 300
   ```

8. Verify `/health`, `/ready`, the Admin Console, Owner login, worker logs, and one controlled
   tenant flow.
9. Only after successful acceptance, replace the state file atomically with the new exact revision
   and mode 0600. This acknowledges the reviewed manual upgrade; it is not a rollback mechanism:

   ```bash
   env_file=.env.production
   state_file="${env_file}.install-state"
   state_tmp=$(mktemp "${state_file}.tmp.XXXXXX")
   git rev-parse --verify HEAD >"${state_tmp}"
   chmod 600 "${state_tmp}"
   mv -- "${state_tmp}" "${state_file}"
   ```

For an application-only failure with schema compatibility, restore the recorded images and rerun
smoke checks. For an incompatible or destructive migration, stop writes and restore the verified
pre-upgrade backup. Do not assume Alembic downgrade is lossless.

## 7. Validation commands for maintainers

Before review or release, run:

```bash
bash -n scripts/install-linux.sh scripts/validate-production-compose.sh
shellcheck scripts/install-linux.sh scripts/validate-production-compose.sh
bash scripts/validate-production-compose.sh <test-production-env>
```

Also run backend Ruff/Pyright/pytest, Web lint/typecheck/build, a clean disposable database migration
plus `alembic check`, and a Docker smoke that verifies internal services have no host bindings.
