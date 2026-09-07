# Release and Deployment Runbook

This runbook defines the first production release path for Customers Manager HUB. It is intentionally infrastructure-neutral: the repository publishes versioned OCI images, while the target Docker host, DNS, TLS termination, firewall, and secret store remain deployment concerns.

## 1. Release invariant

A release is valid only when all of the following are true:

- the release commit is the current `main` commit;
- the requested tag matches `pyproject.toml` (`v0.1.0` for the first MVP release);
- CI has succeeded on that exact `main` commit;
- Security has succeeded on that exact `main` commit;
- the Release workflow builds images from that exact commit.

The Release workflow enforces these checks before publishing.

## 2. Published artifacts

For version `0.1.0`, the Release workflow publishes:

```text
ghcr.io/pooyahayati/customers-manager-hub-api:0.1.0
ghcr.io/pooyahayati/customers-manager-hub-web:0.1.0
ghcr.io/pooyahayati/customers-manager-hub-postgres:0.1.0
```

The worker uses the same application image as the API with the worker command supplied by Compose.

The workflow also creates GitHub Release `v0.1.0` and attaches `release-manifest.txt` containing the source SHA and exact image coordinates.

If GHCR package visibility requires authentication on the deployment host, authenticate with a read-only package credential before pulling images.

## 3. Production configuration

Start from `.env.example`, but do not copy development credentials into production.

At minimum, configure:

```text
APP_ENV=production
APP_DEBUG=false
DATABASE_URL=postgresql+psycopg://<user>:<strong-password>@postgres:5432/<database>
POSTGRES_USER=<user>
POSTGRES_PASSWORD=<strong-password>
POSTGRES_DB=<database>
REDIS_URL=redis://redis:6379/0
ENCRYPTION_KEY=<generated-32-byte-url-safe-base64-key>
TELEGRAM_WEBHOOK_BASE_URL=https://<public-api-host>
S3_ENDPOINT_URL=<production-object-storage-endpoint>
S3_ACCESS_KEY_ID=<secret-store-value>
S3_SECRET_ACCESS_KEY=<secret-store-value>
S3_BUCKET=<bucket>
OPENAI_API_KEY=<optional-secret-store-value>
GOOGLE_GEMINI_API_KEY=<optional-secret-store-value>
```

Production configuration validation rejects known development defaults, missing encryption material, insecure configured S3 endpoints, and non-HTTPS Telegram webhook base URLs.

Do not commit the production `.env` file. Prefer the platform's secret store or a root-readable deployment environment file outside the repository checkout.

## 4. Select release images

On the deployment host:

```bash
export CMH_API_IMAGE=ghcr.io/pooyahayati/customers-manager-hub-api:0.1.0
export CMH_WEB_IMAGE=ghcr.io/pooyahayati/customers-manager-hub-web:0.1.0
export CMH_POSTGRES_IMAGE=ghcr.io/pooyahayati/customers-manager-hub-postgres:0.1.0
```

Validate the merged Compose model:

```bash
docker compose -f compose.yaml -f compose.release.yaml config --quiet
```

Pull release images before changing running services:

```bash
docker compose -f compose.yaml -f compose.release.yaml pull api worker web postgres
```

## 5. First deployment

Start infrastructure first:

```bash
docker compose -f compose.yaml -f compose.release.yaml up -d --no-build postgres redis object-storage
```

Apply migrations explicitly:

```bash
docker compose -f compose.yaml -f compose.release.yaml run --rm api alembic upgrade head
```

Start application services from the published images:

```bash
docker compose -f compose.yaml -f compose.release.yaml up -d --no-build api worker web
```

Verify container state:

```bash
docker compose -f compose.yaml -f compose.release.yaml ps
```

PostgreSQL, Redis, and the reference object-storage host ports are loopback-bound by default. Keep them private. Put the public API/Admin endpoints behind the deployment's HTTPS reverse proxy or load balancer and restrict direct host-port exposure with the host firewall/security group.

## 6. Upgrade deployment

Before a migration-bearing upgrade, create a database backup using the repository backup script and retain it outside the deployment host's ephemeral filesystem:

```bash
bash scripts/backup_postgres.sh /secure/backup/customers-manager-hub-pre-upgrade.dump
```

Then:

1. set `CMH_*_IMAGE` variables to the new immutable version;
2. pull the new images;
3. apply migrations;
4. restart API/Worker/Web with `--no-build`;
5. run the deployment smoke checks;
6. inspect logs and analytics before declaring the deployment complete.

Do not use migration downgrade as an automatic rollback mechanism. If a release introduced an incompatible data migration, restore the verified pre-upgrade backup according to `docs/operations/PRODUCTION-RUNBOOK.md`.

## 7. Deployment smoke

Run unauthenticated infrastructure/application checks:

```bash
bash scripts/smoke_deployment.sh https://api.example.com https://admin.example.com
```

For an authenticated smoke, provide a dedicated low-privilege/operator test account through the process environment:

```bash
SMOKE_EMAIL='smoke@example.com' \
SMOKE_PASSWORD='...' \
SMOKE_REQUIRE_AUTH=1 \
bash scripts/smoke_deployment.sh https://api.example.com https://admin.example.com
```

The script validates:

- API `/health`;
- API `/ready`;
- Admin Console root;
- optional login/session;
- optional active tenant membership.

The password is never printed by the script.

## 8. Real AI provider smoke

CI deliberately uses deterministic adapters and does not consume production AI credentials. After deployment, validate at least one real provider through the application runtime:

1. configure `OPENAI_API_KEY`, `GOOGLE_GEMINI_API_KEY`, or both in the deployment secret store;
2. restart API and Worker so they receive the current secret values;
3. configure the validation tenant's `customer_response` task profile to a supported provider/model;
4. publish/assign the validation Agent and Prompt;
5. send a non-sensitive test question through Website Chat or Telegram;
6. confirm a successful `AIExecutionTrace` with the expected provider/model/task;
7. confirm the response returns through the originating channel;
8. confirm no raw provider key appears in logs, traces, audit details, prompts, or API responses.

If fallback routing is configured, perform one controlled fallback test in a non-customer production validation conversation before enabling broad traffic.

## 9. Telegram smoke

Using a dedicated validation bot/channel when possible:

1. configure the Telegram channel token through the application channel configuration API;
2. verify webhook registration points to the production HTTPS API hostname;
3. send one text message;
4. send one short voice message;
5. confirm canonical contact/conversation/message persistence;
6. confirm voice transcription and normal Agent routing;
7. confirm the final response is delivered back to Telegram;
8. trigger one handoff condition and confirm autonomous response pauses while human ownership is active.

Do not paste the Telegram bot token into issue comments, release notes, workflow logs, or screenshots.

## 10. Website Chat smoke

For the production validation tenant:

1. configure an allowed production origin;
2. start a visitor session from that origin;
3. send a test message;
4. confirm the response returns to Website Chat;
5. confirm the contact/conversation is visible to operators;
6. verify the browser cannot start a session from an unapproved origin.

## 11. Observability acceptance

Before declaring the release deployed, confirm:

- API and worker logs contain request/job correlation identifiers;
- no recurring DLQ/retry exhaustion is present;
- AI usage and cost metrics are populated for priced models;
- handoff/tool success metrics are plausible for smoke traffic;
- audit events exist for release-validation configuration mutations;
- backup location and restore procedure are known to the operator.

## 12. Rollback

For an application-only rollback with schema compatibility:

1. point `CMH_API_IMAGE`, `CMH_WEB_IMAGE`, and `CMH_POSTGRES_IMAGE` to the last known-good version;
2. pull those images;
3. run `docker compose ... up -d --no-build`;
4. rerun `scripts/smoke_deployment.sh`.

For a database-incompatible rollback, stop write traffic and restore the verified pre-upgrade backup. Never blindly run Alembic downgrade against production data.

## 13. Deployment limitation

The repository intentionally does not contain a target server, cloud account, DNS zone, TLS certificate, SSH credential, or production provider secret. Actual deployment to a specific external environment requires those environment coordinates and credentials; they must remain outside source control.
