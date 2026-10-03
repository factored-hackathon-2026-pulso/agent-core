# Utilidades comunes de los scripts E2E (se cargan con `. $PSScriptRoot\_env.ps1`).
$ErrorActionPreference = "Continue"  # docker escribe avisos a stderr: se comprueba $LASTEXITCODE a mano
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$StateDir = Join-Path $Root ".e2e"
$EnvFile = Join-Path $PSScriptRoot ".env.e2e"

function Import-E2EEnv {
    if (-not (Test-Path $EnvFile)) { throw "Falta $EnvFile. Corre primero scripts\e2e\setup.ps1" }
    foreach ($line in Get-Content $EnvFile) {
        $text = ($line -replace "\s+#.*$", "").Trim()
        if (-not $text -or $text.StartsWith("#")) { continue }
        $name, $value = $text -split "=", 2
        $value = $value.Trim()
        # Expande ${OTRA_VARIABLE} con lo ya cargado.
        $value = [regex]::Replace($value, '\$\{(\w+)\}', {
            param($m) [Environment]::GetEnvironmentVariable($m.Groups[1].Value)
        })
        if ($value) { Set-Item -Path "Env:$name" -Value $value }
    }
}

function New-Key32 {
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    [Convert]::ToBase64String($bytes)
}

# docker compose del stack E2E: proyecto y archivo propios (puerto 55432), no toca el docker-compose.yml del repo.
function Dc {
    docker compose -f (Join-Path $PSScriptRoot "compose.yml") -p agentcore-e2e @args
}
