[CmdletBinding()]
param(
    [string]$Python = $env:PYTHON_BIN,
    [string]$ContainerName = $(if ($env:TEST_POSTGRES_CONTAINER) { $env:TEST_POSTGRES_CONTAINER } else { "nckuall-test-postgres" }),
    [string]$PostgresImage = $(if ($env:TEST_POSTGRES_IMAGE) { $env:TEST_POSTGRES_IMAGE } else { "pgvector/pgvector:pg16" }),
    [string]$PostgresUser = $(if ($env:TEST_POSTGRES_USER) { $env:TEST_POSTGRES_USER } else { "nckuall" }),
    [string]$PostgresPassword = $(if ($env:TEST_POSTGRES_PASSWORD) { $env:TEST_POSTGRES_PASSWORD } else { "nckuall" }),
    [string]$PostgresDb = $(if ($env:TEST_POSTGRES_DB) { $env:TEST_POSTGRES_DB } else { "nckuall_test" }),
    [int]$PostgresPort = $(if ($env:TEST_POSTGRES_PORT) { [int]$env:TEST_POSTGRES_PORT } else { 55432 }),
    [string]$ReportFile = $(if ($env:TEST_REPORT_FILE) { $env:TEST_REPORT_FILE } else { "test-report.txt" })
)

$ErrorActionPreference = "Stop"
$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RootDir

Write-Host "==> NCKUall backend clean setup and test"
Write-Host "==> Working directory: $RootDir"

if ([string]::IsNullOrWhiteSpace($Python)) {
    $Python = "python"
}

function Test-CommandAvailable {
    param([Parameter(Mandatory = $true)][string]$Command)
    $null -ne (Get-Command $Command -ErrorAction SilentlyContinue)
}

if (-not (Test-CommandAvailable $Python)) {
    throw "$Python was not found. Install Python 3.11 and rerun this script."
}

$PythonVersion = & $Python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($PythonVersion -ne "3.11") {
    throw "Python 3.11 is required. Current interpreter is Python $PythonVersion."
}

$VenvDir = Join-Path $RootDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$ActivateScript = Join-Path $VenvDir "Scripts\Activate.ps1"

if (Test-Path $VenvDir) {
    $resolvedVenv = (Resolve-Path $VenvDir).Path
    $resolvedRoot = (Resolve-Path $RootDir).Path
    if (-not $resolvedVenv.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove virtual environment outside backend directory: $resolvedVenv"
    }
    Write-Host "==> Removing old virtual environment at $VenvDir"
    Remove-Item -LiteralPath $VenvDir -Recurse -Force
}

Write-Host "==> Creating clean Python 3.11 virtual environment at $VenvDir"
& $Python -m venv $VenvDir

if (Test-Path $ActivateScript) {
    try {
        . $ActivateScript
    } catch {
        Write-Warning "PowerShell execution policy blocked venv activation. Continuing with explicit venv python."
    }
}

Write-Host "==> Upgrading pip tooling"
& $VenvPython -m pip install --upgrade pip setuptools wheel

Write-Host "==> Installing runtime dependencies from requirements.txt"
& $VenvPython -m pip install -r requirements.txt

Write-Host "==> Installing test dependencies from requirements-dev.txt"
& $VenvPython -m pip install -r requirements-dev.txt

$DockerAvailable = $false
if (Test-CommandAvailable "docker") {
    try {
        docker info *> $null
        $DockerAvailable = $true
    } catch {
        $DockerAvailable = $false
    }
}

if ($DockerAvailable) {
    Write-Host "==> Docker is running. Preparing temporary pgvector PostgreSQL container."
    docker pull $PostgresImage

    $existingContainers = docker ps -a --format "{{.Names}}"
    $runningContainers = docker ps --format "{{.Names}}"

    if ($existingContainers -contains $ContainerName) {
        if ($runningContainers -notcontains $ContainerName) {
            Write-Host "==> Starting existing container $ContainerName"
            docker start $ContainerName | Out-Null
        } else {
            Write-Host "==> Container $ContainerName is already running"
        }
    } else {
        Write-Host "==> Creating container $ContainerName on localhost:$PostgresPort"
        docker run `
            --name $ContainerName `
            -e POSTGRES_USER=$PostgresUser `
            -e POSTGRES_PASSWORD=$PostgresPassword `
            -e POSTGRES_DB=$PostgresDb `
            -p "$PostgresPort`:5432" `
            -d $PostgresImage | Out-Null
    }

    Write-Host "==> Waiting for PostgreSQL to accept connections"
    $ready = $false
    for ($attempt = 1; $attempt -le 45; $attempt++) {
        docker exec $ContainerName pg_isready -U $PostgresUser -d $PostgresDb *> $null
        if ($LASTEXITCODE -eq 0) {
            $ready = $true
            break
        }
        Start-Sleep -Seconds 1
    }

    if (-not $ready) {
        throw "PostgreSQL did not become ready in time."
    }

    $env:TEST_DATABASE_URL = "postgresql+psycopg://$PostgresUser`:$PostgresPassword@127.0.0.1:$PostgresPort/$PostgresDb"
    $env:DATABASE_URL = $env:TEST_DATABASE_URL
    $env:DATABASE_READ_URL = $env:TEST_DATABASE_URL
    Write-Host "==> TEST_DATABASE_URL=$($env:TEST_DATABASE_URL)"
} else {
    Write-Warning "Docker is not available or not running."
    if ([string]::IsNullOrWhiteSpace($env:TEST_DATABASE_URL)) {
        Write-Warning "TEST_DATABASE_URL is not set. PostgreSQL integration tests will be skipped by pytest fixtures."
    } else {
        $env:DATABASE_URL = $env:TEST_DATABASE_URL
        $env:DATABASE_READ_URL = $env:TEST_DATABASE_URL
        Write-Host "==> Using existing TEST_DATABASE_URL from environment."
    }
}

if ([string]::IsNullOrWhiteSpace($env:SUPABASE_JWT_SECRET)) {
    $env:SUPABASE_JWT_SECRET = "test-only-supabase-jwt-secret"
}
if ([string]::IsNullOrWhiteSpace($env:SUPABASE_JWT_AUDIENCE)) {
    $env:SUPABASE_JWT_AUDIENCE = "authenticated"
}
if ([string]::IsNullOrWhiteSpace($env:CHAT_MODERATION_ENABLED)) {
    $env:CHAT_MODERATION_ENABLED = "false"
}
if ([string]::IsNullOrWhiteSpace($env:RAG_RERANK_PRELOAD)) {
    $env:RAG_RERANK_PRELOAD = "false"
}
if ([string]::IsNullOrWhiteSpace($env:CHECK_VECTOR_INDEXES_ON_STARTUP)) {
    $env:CHECK_VECTOR_INDEXES_ON_STARTUP = "false"
}
if ($null -eq $env:REDIS_URL) {
    $env:REDIS_URL = ""
}

Write-Host "==> Running pytest"
$ReportPath = Join-Path $RootDir $ReportFile
& $VenvPython -m pytest -v --durations=5 2>&1 |
    Tee-Object -FilePath $ReportPath
$PytestExit = $LASTEXITCODE

Write-Host "==> Text report: $ReportPath"

if ($PytestExit -ne 0) {
    throw "Tests failed with exit code $PytestExit"
}

Write-Host "==> All backend tests passed."
