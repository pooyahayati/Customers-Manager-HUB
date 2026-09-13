# Windows Docker Desktop Runbook

This is the supported local Windows path for Customers Manager HUB. It uses
Docker Desktop with the WSL 2 backend and Linux containers. No Python, Node.js,
pnpm, PostgreSQL, or Redis installation is required on Windows.

## Prerequisites

- Windows 10/11 with current Windows updates.
- Docker Desktop with **Use the WSL 2 based engine** enabled.
- Docker Desktop running in **Linux containers** mode.
- PowerShell 5.1 or newer.

The repository may remain at:

~~~text
W:\My Program\Customers Manager HUB
~~~

The Compose stack does not mount the source tree into running containers. Docker
only needs to read the directory as a build context. If W: is a mapped network
drive, Docker Desktop and PowerShell must run under the same Windows account.

## First run

Open PowerShell:

~~~powershell
Set-Location -LiteralPath 'W:\My Program\Customers Manager HUB'
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\docker-windows.ps1 setup
.\docker-windows.ps1 up
~~~

After the first setup, the normal one-click entrypoint is:

~~~text
Start-Customers-Manager-HUB.cmd
~~~

Double-click it in File Explorer. The launcher starts Docker Desktop when
needed, starts the stack, waits for the Admin UI, and opens
http://localhost:3000 in Google Chrome. If Chrome is unavailable, it uses the
Windows default browser.

The setup action creates .env from .env.example when needed and generates a
valid development-only encryption key. It never replaces an existing key.

The first up builds all local images, starts PostgreSQL/pgvector, Redis, and
SeaweedFS, applies Alembic migrations, and then starts the API, Worker, and Web
services.

## Create the initial owner

Bootstrap is deliberately interactive and works only while the identity
database is empty:

~~~powershell
.\docker-windows.ps1 bootstrap `
  -TenantName 'My Company' `
  -TenantSlug 'my-company' `
  -Email 'owner@example.com'
~~~

Enter and confirm the password at the prompts. Then open:

- Admin UI: <http://localhost:3000>
- API documentation: <http://localhost:8000/docs>

## Daily commands

~~~powershell
# Start; use -NoBuild after the images already exist
.\docker-windows.ps1 up -NoBuild

# Check container health
.\docker-windows.ps1 status

# Follow all logs, or only one service
.\docker-windows.ps1 logs
.\docker-windows.ps1 logs -Service api

# Print logs once without following
.\docker-windows.ps1 logs -Service web -NoFollow

# Apply migrations explicitly
.\docker-windows.ps1 migrate

# Stop without deleting volumes or customer data
.\docker-windows.ps1 down
~~~

The wrapper intentionally has no volume-deletion command. Destructive cleanup
must remain an explicit manual operation using the production runbook's backup
guidance.

## Configuration

Edit .env before up when ports or provider credentials are needed:

~~~dotenv
WEB_PORT=3000
API_PORT=8000
OPENAI_API_KEY=
GOOGLE_GEMINI_API_KEY=
~~~

The browser sends API requests to the Next.js same-origin gateway. Inside
Compose, the Web service reaches FastAPI through http://api:8000; the internal
container address is not exposed to browser JavaScript.

## Troubleshooting

### Docker API or named-pipe error

Start Docker Desktop and wait until it reports that the engine is running. The
script checks the engine before build or startup.

### Linux containers required

Switch Docker Desktop to Linux containers. The pinned Python, Node.js,
PostgreSQL/pgvector, Redis, and SeaweedFS images are Linux images.

### Port already in use

Choose unused ports in .env, for example:

~~~dotenv
WEB_PORT=3100
API_PORT=8100
POSTGRES_PORT=55432
REDIS_PORT=56379
S3_PORT=58333
~~~

### Rebuild after source changes

Run:

~~~powershell
.\docker-windows.ps1 up
~~~

For a faster restart without rebuilding unchanged images:

~~~powershell
.\docker-windows.ps1 restart -NoBuild
~~~
