param(
    [string]$OpenApiUrl = "http://127.0.0.1:8000/openapi.json",
    [string]$OutputDirectory = ""
)

$ErrorActionPreference = "Stop"
$BackendRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$WorkspaceRoot = (Resolve-Path (Join-Path $BackendRoot "..")).Path

if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $WorkspaceRoot "sdks\nckuall-typescript"
}

$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
$SpecFile = Join-Path ([System.IO.Path]::GetTempPath()) "nckuall-openapi.json"

try {
    Invoke-WebRequest -Uri $OpenApiUrl -OutFile $SpecFile
    npx --yes @openapitools/openapi-generator-cli generate `
        -i $SpecFile `
        -g typescript-axios `
        -o $OutputDirectory `
        --additional-properties=npmName=@nckuall/api-client,npmVersion=1.0.0,supportsES6=true,withSeparateModelsAndApi=true,apiPackage=api,modelPackage=models

    if ($LASTEXITCODE -ne 0) {
        throw "OpenAPI Generator exited with code $LASTEXITCODE."
    }
    Write-Host "SDK generated at $OutputDirectory"
}
finally {
    if (Test-Path -LiteralPath $SpecFile) {
        Remove-Item -LiteralPath $SpecFile -Force
    }
}
