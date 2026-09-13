[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = $PSScriptRoot
$DockerScript = Join-Path $ProjectRoot "docker-windows.ps1"
$ApplicationUrl = "http://localhost:3000"
$SkipBrowser = $env:CMH_LAUNCHER_SKIP_BROWSER -eq "1"
$PreviousLocation = Get-Location

function Test-DockerEngine {
    & docker info *> $null
    return $LASTEXITCODE -eq 0
}

function Start-DockerDesktop {
    $Candidates = @(
        (Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"),
        (Join-Path $env:LOCALAPPDATA "Docker\Docker Desktop.exe")
    )
    $DockerDesktop = $Candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1

    if ($null -eq $DockerDesktop) {
        Write-Host "Docker Desktop could not be started automatically." -ForegroundColor Yellow
        Write-Host "Please start Docker Desktop manually."
        return
    }

    Write-Host "Docker Desktop is not running. Starting it now..."
    Start-Process -FilePath $DockerDesktop -WindowStyle Hidden
}

function Wait-ForDocker {
    if (Test-DockerEngine) {
        return
    }

    Start-DockerDesktop
    Write-Host "Waiting for the Linux container engine..."
    for ($Attempt = 0; $Attempt -lt 90; $Attempt++) {
        Start-Sleep -Seconds 2
        if (Test-DockerEngine) {
            return
        }
    }

    throw "Docker Desktop did not become ready within three minutes."
}

function Start-Application {
    try {
        & $DockerScript up -NoBuild
    }
    catch {
        Write-Host ""
        Write-Host "A quick start was not possible. Building the required images..." -ForegroundColor Yellow
        & $DockerScript up
    }
}

function Wait-ForWeb {
    Write-Host ""
    Write-Host "Waiting for the Admin UI..."
    for ($Attempt = 0; $Attempt -lt 60; $Attempt++) {
        try {
            $Response = Invoke-WebRequest -UseBasicParsing -Uri $ApplicationUrl -TimeoutSec 2
            if ($Response.StatusCode -eq 200) {
                return
            }
        }
        catch {
            # The service may still be inside its Docker health-check start period.
        }
        Start-Sleep -Seconds 2
    }

    throw "The containers started, but the Admin UI did not become ready."
}

function Open-Application {
    if ($SkipBrowser) {
        return
    }

    $ChromeCandidates = @(
        (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe"),
        (Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe")
    )
    $Chrome = $ChromeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1

    if ($null -ne $Chrome) {
        Start-Process -FilePath $Chrome -ArgumentList @("--new-window", $ApplicationUrl)
        return
    }

    Write-Host "Google Chrome was not found. Opening the Windows default browser..."
    Start-Process $ApplicationUrl
}

try {
    Set-Location -LiteralPath $ProjectRoot

    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "Docker CLI was not found. Install Docker Desktop and try again."
    }

    Write-Host ""
    Write-Host " Customers Manager HUB" -ForegroundColor Cyan
    Write-Host " ---------------------"
    Write-Host ""

    Wait-ForDocker
    Write-Host "Docker Desktop is ready." -ForegroundColor Green
    Start-Application
    Wait-ForWeb

    Write-Host "The application is ready at $ApplicationUrl" -ForegroundColor Green
    Open-Application
    exit 0
}
catch {
    Write-Host ""
    Write-Host "Startup failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Run '.\docker-windows.ps1 logs' for service logs."
    exit 1
}
finally {
    Set-Location -LiteralPath $PreviousLocation
}
