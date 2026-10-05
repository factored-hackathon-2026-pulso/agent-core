<#
Check end-to-end del stack local: arranca un `agentcore serve` propio (puerto 8010, con el código de ESTE
checkout), corre scripts\e2e\check.py contra él y lo apaga. No toca el `serve` que tengas abierto en 8000.

  .\scripts\e2e\check.ps1                   # plataforma y seguridad: sin keys, sin costo
  .\scripts\e2e\check.ps1 -Llm              # + una llamada real al llm-gateway (llm-smoke, cuesta centavos)
  .\scripts\e2e\check.ps1 -Conversations    # + escenarios del runbook con JEV y el LLM (necesita la key de JEV)

Antes: setup.ps1 una vez (Postgres en 55432, registry importado, llm-gateway). Lee scripts\e2e\.env.e2e; lo que
falte toma el valor por defecto del stack local, y las claves de huellas, si faltan, se generan solo para esta
corrida (avisa). El token del gateway sale de .e2e\gateway.env si no está en el entorno (-GatewayEnv para otra ruta).
Sale con 0 si nada falló; el log del servidor queda en .e2e\check-serve.log.
#>
param(
    [int]$Port = 8010,
    [switch]$Llm,
    [switch]$Conversations,
    [string]$GatewayEnv = ""
)
. $PSScriptRoot\_env.ps1
Set-Location $Root
if ((Test-Path $EnvFile) -and ((Get-Item $EnvFile).Length -gt 8)) { Import-E2EEnv }
else { Write-Warning "$EnvFile no existe o está vacío: uso los valores por defecto del stack local." }

function Set-Default([string]$Name, [string]$Value) {
    if (-not [Environment]::GetEnvironmentVariable($Name)) { Set-Item -Path "Env:$Name" -Value $Value }
}
Set-Default "AGENTCORE_REGISTRY_DSN" "postgresql://agentcore:agentcore-dev-only@127.0.0.1:55432/agentcore"
Set-Default "AGENTCORE_EVAL_DSN" "postgresql://agentcore:agentcore-dev-only@127.0.0.1:55432/agentcore_eval"
Set-Default "AGENTCORE_LLM_GATEWAY_URL" "http://127.0.0.1:8080"
Set-Default "AGENTCORE_ALLOW_DEMO" "1"
foreach ($pair in @(@("AGENTCORE_KEYS_FINGERPRINT", "check-fp"), @("AGENTCORE_KEYS_TOKEN_MAP", "check-tm"))) {
    if (-not [Environment]::GetEnvironmentVariable($pair[0])) {
        Set-Item -Path "Env:$($pair[0])" -Value "$($pair[1]):$(New-Key32)"
        Write-Warning "$($pair[0]) no estaba definida: uso una clave efímera solo para esta corrida."
    }
}
if (-not $env:AGENTCORE_LLM_GATEWAY_TOKEN) {
    $file = if ($GatewayEnv) { $GatewayEnv } else { Join-Path $StateDir "gateway.env" }
    if (Test-Path $file) {
        $line = Get-Content $file | Where-Object { $_ -match "^GATEWAY_TOKEN_AGENT_CORE=" } | Select-Object -First 1
        if ($line) { $env:AGENTCORE_LLM_GATEWAY_TOKEN = ($line -split "=", 2)[1].Trim() }
    }
    if (-not $env:AGENTCORE_LLM_GATEWAY_TOKEN) {
        Write-Warning "Sin token del llm-gateway: toda generación caerá a plantilla."
        Remove-Item Env:AGENTCORE_LLM_GATEWAY_URL -ErrorAction SilentlyContinue
    }
}

# Claves públicas de identidad (las TEST del repo; las mismas que emite testing.demo_identities).
New-Item -ItemType Directory -Force $StateDir | Out-Null
$identityKeys = Join-Path $StateDir "identity-keys.json"
$staffKeys = Join-Path $StateDir "staff-keys.json"
if (-not ((Test-Path $identityKeys) -and (Test-Path $staffKeys))) {
    uv run python -m testing.demo_identities --public-keys $identityKeys --staff-keys $staffKeys | Out-Null
}

# El stack de fondo tiene que estar arriba.
$pg = docker ps --filter "name=agentcore-e2e-postgres" --filter "health=healthy" --format "{{.Names}}"
if (-not $pg) { throw "Postgres del e2e no está arriba (puerto 55432): corre scripts\e2e\setup.ps1" }

$log = Join-Path $StateDir "check-serve.log"
$serveArgs = @("run", "agentcore", "serve", "--host", "127.0.0.1", "--port", "$Port",
    "--identity-keys", $identityKeys, "--staff-keys", $staffKeys, "--registry-api",
    "--lang-thresholds", (Join-Path $Root "scripts\e2e\lang-thresholds.json"),
    "--agents", "recepcion,disputas,consultas,copiloto-asesor,constructor-chat",
    "--tools", "testing.e2e_demo:tools", "--classifier", "testing.e2e_demo:classifier_provider",
    "--field-classifier", "testing.e2e_demo:field_classifier", "--calibration", "testing.e2e_demo:calibration")
$serve = Start-Process -FilePath "uv" -ArgumentList $serveArgs -RedirectStandardOutput $log `
    -RedirectStandardError "$log.err" -NoNewWindow -PassThru
$exit = 1
try {
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 1
        if ($serve.HasExited) { break }
        try {
            $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "http://127.0.0.1:$Port/readyz"
            if ($r.StatusCode -eq 200) { $ready = $true; break }
        } catch { }
    }
    if (-not $ready) {
        Get-Content "$log.err" -Tail 20
        throw "serve no quedó listo en 60 s (log: $log.err)"
    }
    if ($Llm -or $Conversations) {
        Write-Host "`n== llm-gateway: una llamada real (llm-smoke --n 1) ==" -ForegroundColor Cyan
        uv run agentcore llm-smoke --registry tests/fixtures/registry-e2e --profile perfil-generacion@1.0.0 --n 1
        if ($LASTEXITCODE -ne 0) { Write-Host "[FALLA] llm-smoke" -ForegroundColor Red }
        $smokeExit = $LASTEXITCODE
    } else { $smokeExit = 0 }
    Write-Host "`n== agent-core: check HTTP contra http://127.0.0.1:$Port ==" -ForegroundColor Cyan
    $checkArgs = @("run", "python", "scripts/e2e/check.py", "--base-url", "http://127.0.0.1:$Port")
    if ($Conversations) { $checkArgs += "--conversations" }
    & uv @checkArgs
    $exit = if ($LASTEXITCODE -ne 0 -or $smokeExit -ne 0) { 1 } else { 0 }
} finally {
    if (-not $serve.HasExited) { taskkill /PID $serve.Id /T /F *> $null }
}
exit $exit
