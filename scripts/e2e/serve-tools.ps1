<#
Arranca `agentcore serve` con las tools REALES (servicio tool-service sobre gold_restricted) en vez de los dobles
de tools y del catálogo de campos. El authz es el `PolicyAuthz` real; transcript y calibración siguen siendo dobles de demo.

`grant_active` es el real (la plataforma) si AGENTCORE_GRANTS_URL y AGENTCORE_GRANTS_TOKEN están definidas; si no, usa el
doble de demo, que da por activa toda delegación firmada, y lo avisa al arrancar. Sin AGENTCORE_AUTHZ_FIELD_GRANTS_FILE
nadie lee campos de clientes (todo sale enmascarado): también se avisa.

Antes: el servicio en marcha (en el repo tool-service):
  $env:TOOL_DATA_DIR = "<data-pipeline>\data"; $env:TOOL_SERVICE_TOKENS = "agent-core:<token>"; $env:PORT = 8095
  uv run tool-service
y las tools importadas al registry (docs\runbook-e2e.md §8). El token va en AGENTCORE_TOOL_SERVICE_TOKEN (o en
scripts\e2e\.env.e2e).
#>
param(
    [string]$ToolServiceUrl = "http://127.0.0.1:8095",
    [string]$DataDir = (Join-Path (Split-Path $PSScriptRoot -Parent | Split-Path -Parent | Split-Path -Parent) "data-pipeline\data")
)
. $PSScriptRoot\_env.ps1
Set-Location $Root
Import-E2EEnv
if (-not $env:AGENTCORE_JEV_API_KEY) { throw "Falta AGENTCORE_JEV_API_KEY en $EnvFile (serve la exige)." }
if (-not $env:AGENTCORE_TOOL_SERVICE_TOKEN) { throw "Falta AGENTCORE_TOOL_SERVICE_TOKEN (el token de agent-core en el tool-service)." }
$env:AGENTCORE_TOOL_SERVICE_URL = $ToolServiceUrl
$env:AGENTCORE_AUTHZ_BIND_KEYS = "subject_ref,customer_id"  # el tool-service acepta cualquiera de los dos nombres

# El catálogo de clasificación sale de la última corrida publicada por data-pipeline, más lo propio del motor.
$pointer = Get-Content (Join-Path $DataDir "publish\latest.json") -Raw | ConvertFrom-Json
$published = Join-Path $DataDir ($pointer.path + "\field_classification.json")
if (-not (Test-Path $published)) { throw "No existe $published" }
$env:AGENTCORE_FIELD_CLASSIFICATION_FILES = "$published,$Root\scripts\e2e\field-overlay.json"

$grantArgs = @()
if ($env:AGENTCORE_GRANTS_URL -and $env:AGENTCORE_GRANTS_TOKEN) {
    $grantArgs = @("--grant-active", "agent_core.adapters.grants:http_grant_active")
} else {
    Write-Warning ("grant_active es el DOBLE de demo: toda delegación firmada cuenta como activa (una revocación no " +
        "se ve). Define AGENTCORE_GRANTS_URL y AGENTCORE_GRANTS_TOKEN para usar la plataforma.")
}
if (-not $env:AGENTCORE_AUTHZ_FIELD_GRANTS_FILE) {
    Write-Warning ("Sin AGENTCORE_AUTHZ_FIELD_GRANTS_FILE nadie lee campos de clientes: las respuestas salen enmascaradas.")
}

uv run agentcore serve `
    --identity-keys "$StateDir\identity-keys.json" --staff-keys "$StateDir\staff-keys.json" `
    --registry-api --lang-thresholds "$Root\scripts\e2e\lang-thresholds.json" `
    --agents "recepcion,disputas,consultas,copiloto-asesor,constructor-chat" `
    --tools agent_core.adapters.tools:http_tool_executor `
    --authz agent_core.adapters.policy_authz:policy_authz `
    --field-classifier agent_core.composition.classification:field_classifier `
    --classifier testing.e2e_demo:classifier_provider --calibration testing.e2e_demo:calibration `
    @grantArgs
