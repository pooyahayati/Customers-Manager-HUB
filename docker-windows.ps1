[CmdletBinding()]
param(
    [ValidateSet("setup", "up", "down", "restart", "status", "logs", "migrate", "bootstrap", "validate")]
    [string]$Action = "up",

    [string]$TenantName,
    [string]$TenantSlug,
    [string]$Email,
    [string]$Service,
    [switch]$NoBuild,
    [switch]$NoFollow
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = $PSScriptRoot
$ComposeFile = Join-Path $ProjectRoot "compose.yaml"
$EnvironmentFile = Join-Path $ProjectRoot ".env"
$EnvironmentTemplate = Join-Path $ProjectRoot ".env.example"
$PreviousLocation = Get-Location

function Assert-DockerCli {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "Docker CLI was not found. Install Docker Desktop for Windows and reopen PowerShell."
    }

    & docker compose version *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose v2 is not available. Update Docker Desktop."
    }
}

function Assert-DockerEngine {
    $ServerOs = (& docker info --format "{{.OSType}}" 2>$null)
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Desktop is not running. Start Docker Desktop, wait until the engine is ready, and retry."
    }

    if ($ServerOs.Trim() -ne "linux") {
        throw "Customers Manager HUB requires Docker Desktop in Linux containers mode."
    }
}

function Invoke-ComposeCommand {
    param([Parameter(Mandatory = $true)][string[]]$ComposeArguments)

    & docker compose -f $ComposeFile @ComposeArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose command failed: docker compose $($ComposeArguments -join ' ')"
    }
}

function New-DevelopmentEncryptionKey {
    $Bytes = New-Object byte[] 32
    $Generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $Generator.GetBytes($Bytes)
    }
    finally {
        $Generator.Dispose()
    }

    return [Convert]::ToBase64String($Bytes).Replace("+", "-").Replace("/", "_")
}

function Initialize-Environment {
    if (-not (Test-Path -LiteralPath $EnvironmentFile)) {
        Copy-Item -LiteralPath $EnvironmentTemplate -Destination $EnvironmentFile
        Write-Host "Created .env from .env.example" -ForegroundColor Green
    }

    $Content = [System.IO.File]::ReadAllText($EnvironmentFile)
    if ($Content -match "(?m)^ENCRYPTION_KEY=\s*$") {
        $EncryptionKey = New-DevelopmentEncryptionKey
        $Content = [regex]::Replace(
            $Content,
            "(?m)^ENCRYPTION_KEY=\s*$",
            "ENCRYPTION_KEY=$EncryptionKey"
        )
        $Utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($EnvironmentFile, $Content, $Utf8WithoutBom)
        Write-Host "Generated a local development encryption key in .env" -ForegroundColor Green
    }
}

function Test-ComposeConfiguration {
    Invoke-ComposeCommand -ComposeArguments @("config", "--quiet")
    Write-Host "Docker Compose configuration is valid." -ForegroundColor Green
}

function Start-Dependencies {
    Invoke-ComposeCommand -ComposeArguments @(
        "up", "-d", "postgres", "redis", "object-storage"
    )
}

function Invoke-Migrations {
    Invoke-ComposeCommand -ComposeArguments @("run", "--rm", "api", "alembic", "upgrade", "head")
}

function Start-Stack {
    if (-not $NoBuild) {
        Invoke-ComposeCommand -ComposeArguments @("build")
    }

    Start-Dependencies
    Invoke-Migrations
    Invoke-ComposeCommand -ComposeArguments @("up", "-d", "api", "worker", "web")

    Write-Host ""
    Write-Host "Customers Manager HUB is starting:" -ForegroundColor Green
    Write-Host "  Admin UI: http://localhost:3000"
    Write-Host "  API docs: http://localhost:8000/docs"
    Write-Host ""
    Write-Host "Run '.\docker-windows.ps1 status' to check health."
}

try {
    Set-Location -LiteralPath $ProjectRoot
    Assert-DockerCli

    switch ($Action) {
        "setup" {
            Initialize-Environment
            Test-ComposeConfiguration
            Write-Host "Setup is complete. Start Docker Desktop, then run '.\docker-windows.ps1 up'."
        }
        "validate" {
            Initialize-Environment
            Test-ComposeConfiguration
        }
        "up" {
            Initialize-Environment
            Test-ComposeConfiguration
            Assert-DockerEngine
            Start-Stack
        }
        "down" {
            Assert-DockerEngine
            Invoke-ComposeCommand -ComposeArguments @("down")
        }
        "restart" {
            Initialize-Environment
            Test-ComposeConfiguration
            Assert-DockerEngine
            Invoke-ComposeCommand -ComposeArguments @("down")
            Start-Stack
        }
        "status" {
            Assert-DockerEngine
            Invoke-ComposeCommand -ComposeArguments @("ps")
        }
        "logs" {
            Assert-DockerEngine
            $LogArguments = @("logs", "--tail=200")
            if (-not $NoFollow) {
                $LogArguments += "--follow"
            }
            if ($Service) {
                $LogArguments += $Service
            }
            Invoke-ComposeCommand -ComposeArguments $LogArguments
        }
        "migrate" {
            Initialize-Environment
            Test-ComposeConfiguration
            Assert-DockerEngine
            Start-Dependencies
            Invoke-Migrations
        }
        "bootstrap" {
            if (-not $TenantName -or -not $TenantSlug -or -not $Email) {
                throw "bootstrap requires -TenantName, -TenantSlug, and -Email."
            }

            Initialize-Environment
            Test-ComposeConfiguration
            Assert-DockerEngine
            Start-Dependencies
            Invoke-Migrations
            Invoke-ComposeCommand -ComposeArguments @(
                "run", "--rm", "api",
                "python", "-m", "customers_manager_hub.bootstrap",
                "--tenant-name", $TenantName,
                "--tenant-slug", $TenantSlug,
                "--email", $Email
            )
        }
    }
}
finally {
    Set-Location -LiteralPath $PreviousLocation
}
