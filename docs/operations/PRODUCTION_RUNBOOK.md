# Production Operations Runbook

Supported topology: Docker Compose with API, Worker, Web, PostgreSQL + pgvector, Redis Streams/AOF and S3-compatible object storage.

## 1. Production preflight

Before deployment:

- `APP_ENV=production`;
- `APP_DEBUG=false`;
- use generated database credentials, never `change-me`;
- configure a generated 32-byte URL-safe base64 `ENCRYPTION_KEY`;
- use HTTPS for Telegram webhook and externally addressed S3 endpoints;
- replace development S3 credentials;
- configure `TRUSTED_PROXY_CIDRS` only for actual reverse-proxy/load-balancer networks;
- leave `RATE_LIMIT_ENABLED=true`;
- terminate TLS at the trusted ingress and restrict direct API exposure where possible;
- retain CI and Security workflow results for the release commit.

Do not place production credentials in `.env.example`, Git, GitHub Actions logs, prompt text, tool configuration JSON or support tickets.

## 2. Health and readiness

- `GET /health` proves only that the API process is alive.
- `GET /ready` verifies PostgreSQL/pgvector and Redis reachability.
- Docker healthchecks use `/ready` for the API and `/` for Web.
- A failed readiness check should remove the instance from ingress rather than trigger data mutation.

Worker startup also checks PostgreSQL and Redis and exits non-zero if dependencies are unavailable.

## 3. PostgreSQL backup

Create a directory outside the repository and run:

```bash
mkdir -p backups
./scripts/backup_postgres.sh backups/cmh-$(date -u +%Y%m%dT%H%M%SZ).dump
```

The script uses PostgreSQL custom format (`pg_dump -Fc`). Protect backup files as production secrets because they contain tenant/customer data and encrypted credential records.

Recommended minimum: daily logical backup plus infrastructure/provider volume snapshots. Define retention according to tenant/legal requirements.

## 4. Redis and object-storage backup

PostgreSQL is the authoritative transactional source of truth, but production recovery should also snapshot:

- `redis_data` because Redis Streams may contain pending work and AOF state;
- `object_storage_data` because knowledge-source objects live outside PostgreSQL.

For the Compose reference deployment, use storage-provider snapshots or quiesced Docker-volume snapshots. Never copy a live database volume as a substitute for PostgreSQL logical backup.

A Redis loss can leave persisted inbound events without their scheduling entry. Treat queue recovery/reconciliation as an incident operation; do not silently mark events completed.

## 5. Restore validation

Restore into a new database first:

```bash
./scripts/restore_postgres.sh backups/<backup>.dump cmh_restore_validation
```

The restore script refuses to overwrite the primary configured database unless `ALLOW_IN_PLACE_RESTORE=1` is explicitly supplied.

Validate:

```bash
docker compose exec -T postgres psql -U "${POSTGRES_USER:-cmh}" -d cmh_restore_validation -c 'select * from alembic_version;'
```

Then run application smoke checks against an environment configured to use the restored database. A backup is not considered valid until restore has been exercised.

## 6. Database upgrade procedure

1. Stop external write ingress or put the deployment into a controlled maintenance window when the migration requires it.
2. Create and verify a pre-upgrade backup.
3. Pull/build the exact release commit.
4. Run:

```bash
docker compose run --rm api alembic upgrade head
docker compose run --rm api alembic check
```

5. Start/roll API, Worker and Web.
6. Verify `/ready`, Web health and one tenant-authenticated smoke path.
7. Verify worker logs show a healthy consumer start.

Rollback strategy is forward-fix for application-only changes. For unsafe or destructive schema changes, restore the validated pre-upgrade backup rather than assuming Alembic downgrade is lossless.

## 7. Dead-letter handling

Live and dead-letter streams are separate. Retry exhaustion or malformed queue entries move to DLQ and are removed from the live stream.

Operational response:

1. inspect structured worker logs for `event_id`/`source_id`, `attempt`, `error_code` and `stream_id`;
2. correct the underlying dependency/configuration/data issue;
3. inspect the canonical PostgreSQL state before replay;
4. replay only when the domain operation is safe/idempotent;
5. never copy a DLQ item blindly back to the live stream without confirming current database state.

DLQ retention is operational; snapshot/export incident-relevant entries before pruning.

## 8. Rate-limit operations

Application limits protect login, Website Chat public ingress and Telegram webhooks. HTTP 429 includes `Retry-After`.

If legitimate traffic is limited:

- confirm the immediate peer/client IP and trusted-proxy configuration;
- increase limits through environment configuration and restart/roll the API;
- do not disable limits permanently in production.

If Redis is unavailable, the application limiter fails open with a warning while `/ready` reports unhealthy. The edge proxy should provide independent emergency abuse protection.

## 9. Data retention foundation

Data classes:

- authentication/session data: short-lived operational data; expired records can be purged after incident/audit needs;
- AI/tool/retrieval/policy traces: operational/audit data; recommended baseline 90 days unless tenant/legal policy requires longer;
- audit events: recommended baseline 365 days or tenant/legal requirement;
- messages, conversations, contacts and approved customer memory: business records; no automatic deletion in M15;
- knowledge source objects/chunks: business records tied to explicit tenant deletion;
- backups: encrypted/restricted copies with their own retention schedule.

M15 intentionally does not turn on automatic deletion of business records. Configure legal/tenant policy before implementing automated purge.

## 10. Security incidents

For suspected credential exposure:

1. rotate the external provider/channel/tool credential;
2. revoke affected sessions;
3. rotate infrastructure credentials if implicated;
4. preserve relevant audit and structured logs;
5. do not rotate `ENCRYPTION_KEY` casually: existing encrypted credentials depend on its key version. A key-rotation migration must re-encrypt stored secrets deliberately.

For SSRF/tool abuse, disable the affected tool/agent permission first, preserve ToolExecution/Policy traces, then investigate destination configuration.

## 11. Load validation

Use `scripts/load_http.py` only against local/staging environments or production with an approved test window. Start with conservative concurrency. Test:

- `/health` and `/ready` for baseline HTTP overhead;
- Website Chat session/message endpoints with a dedicated test channel;
- Telegram webhook with a dedicated test channel and valid secret.

Never use customer credentials or production conversations for synthetic load.

## 12. Release acceptance

A release is production-eligible only when:

- functional CI passes;
- Security workflow passes or findings have an explicitly reviewed exception;
- migrations pass `upgrade head` and `alembic check`;
- backup/restore smoke has passed on the supported topology;
- API/Worker/Web run non-root;
- no placeholder secrets are accepted by production configuration.