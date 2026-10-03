<#
 Prepara el stack local de la prueba E2E: Postgres, llm-gateway (Docker), migraciones, registry con los agentes
 y las credenciales de prueba. Es idempotente: puedes repetirlo. Después: .\scripts\e2e\serve.ps1
 Opciones:  -ResetDb  borra y recrea las bases (hace falta si cambias una entidad del registry: las versiones
                      son inmutables por hash).
#>
param([switch]$ResetDb)
. $PSScriptRoot\_env.ps1
Set-Location $Root

function Invoke-Checked([scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "Falló: $Command" }
}

# 1. .env.e2e con claves de demo generadas
if (-not (Test-Path $EnvFile)) {
    Copy-Item "$PSScriptRoot\.env.e2e.example" $EnvFile
    $text = Get-Content $EnvFile -Raw
    $text = $text -replace "(?m)^AGENTCORE_KEYS_FINGERPRINT=.*$", "AGENTCORE_KEYS_FINGERPRINT=demo-fp-1:$(New-Key32)"
    $text = $text -replace "(?m)^AGENTCORE_KEYS_TOKEN_MAP=.*$", "AGENTCORE_KEYS_TOKEN_MAP=demo-tm-1:$(New-Key32)"
    $text = $text -replace "(?m)^GATEWAY_TOKEN_AGENT_CORE=.*$", "GATEWAY_TOKEN_AGENT_CORE=$(New-Key32)"
    Set-Content -Path $EnvFile -Value $text -Encoding utf8
    Write-Host "Creé $EnvFile con claves de demo. EDÍTALO y pon OPENROUTER_API_KEY y AGENTCORE_JEV_API_KEY." -ForegroundColor Yellow
}
Import-E2EEnv

# 2. Docker + Postgres (+ base de evaluaciones, distinta de la de producción)
docker info *> $null
if ($LASTEXITCODE -ne 0) { throw "Docker no responde: abre Docker Desktop y repite." }
if ($ResetDb) { Invoke-Checked { Dc down -v } }
Invoke-Checked { Dc up -d postgres }
for ($i = 0; $i -lt 30; $i++) {
    Dc exec -T postgres pg_isready -U agentcore *> $null
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 2
}
$has = Dc exec -T postgres psql -U agentcore -tAc "SELECT 1 FROM pg_database WHERE datname='agentcore_eval'"
if (-not ($has -match "1")) {
    Invoke-Checked { Dc exec -T postgres psql -U agentcore -c "CREATE DATABASE agentcore_eval" }
}

# 3. Dependencias, migraciones, credenciales de prueba y registry
Invoke-Checked { uv sync --locked }
Invoke-Checked { uv run agentcore migrate }
New-Item -ItemType Directory -Force $StateDir | Out-Null
$tokens = uv run python -m testing.demo_identities --public-keys "$StateDir\identity-keys.json" --staff-keys "$StateDir\staff-keys.json"
if ($LASTEXITCODE -ne 0) { throw "No pude emitir las credenciales de prueba" }
Set-Content "$StateDir\tokens.json" -Value $tokens -Encoding utf8
$env:AGENTCORE_CREDENTIAL = (($tokens -join "`n") | ConvertFrom-Json).admin
Invoke-Checked { uv run agentcore registry --verifier testing.registry_demo:demo_verifier import tests/fixtures/registry-e2e }
Remove-Item Env:AGENTCORE_CREDENTIAL

# 4. llm-gateway (contenedor); sin key del proveedor se omite y la generación cae a plantillas
if ($env:OPENROUTER_API_KEY) {
    $gw = (Resolve-Path (Join-Path $Root "..\llm-gateway") -ErrorAction Stop).Path
    Invoke-Checked { docker build -q -t llm-gateway-e2e $gw }
    docker rm -f llm-gateway-e2e *> $null
    $consumers = '{"agent-core":{"token_env":"GATEWAY_TOKEN_AGENT_CORE"}}'
    $endpoints = '{"openrouter":{"base_url":"https://openrouter.ai/api/v1","api_key_env":"OPENROUTER_API_KEY"}}'
    Invoke-Checked {
        docker run -d --name llm-gateway-e2e -p 8080:8080 `
            -e "GATEWAY_CONSUMERS=$consumers" -e "LLM_ENDPOINTS=$endpoints" `
            -e "GATEWAY_TOKEN_AGENT_CORE=$env:GATEWAY_TOKEN_AGENT_CORE" `
            -e "OPENROUTER_API_KEY=$env:OPENROUTER_API_KEY" llm-gateway-e2e
    }
    Write-Host "llm-gateway arriba en http://127.0.0.1:8080"
} else {
    Write-Host "OPENROUTER_API_KEY vacía: llm-gateway NO se levantó (toda generación cae a plantilla)." -ForegroundColor Yellow
}
if (-not $env:AGENTCORE_JEV_API_KEY) {
    Write-Host "AGENTCORE_JEV_API_KEY vacía: serve no arrancará (la exige)." -ForegroundColor Yellow
}
Write-Host "`nListo. Siguiente: .\scripts\e2e\serve.ps1   (otra terminal: .\scripts\e2e\chat.ps1 -As customer -Agent recepcion)" -ForegroundColor Green
