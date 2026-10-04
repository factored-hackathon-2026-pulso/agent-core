<#
Arranca `agentcore serve` con las tools REALES (servicio tool-service sobre gold_restricted) en vez de los dobles
de tools y del catálogo de campos. Authz, transcript y calibración siguen siendo dobles de demo.

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

# El catálogo de clasificación sale de la última corrida publicada por data-pipeline, más lo propio del motor.
$pointer = Get-Content (Join-Path $DataDir "publish\latest.json") -Raw | ConvertFrom-Json
$published = Join-Path $DataDir ($pointer.path + "\field_classification.json")
if (-not (Test-Path $published)) { throw "No existe $published" }
$env:AGENTCORE_FIELD_CLASSIFICATION_FILES = "$published,$Root\scripts\e2e\field-overlay.json"

uv run agentcore serve `
    --identity-keys "$StateDir\identity-keys.json" --staff-keys "$StateDir\staff-keys.json" `
    --registry-api --lang-thresholds "$Root\scripts\e2e\lang-thresholds.json" `
    --agents "recepcion,disputas,consultas,copiloto-asesor,constructor-chat" `
    --tools agent_core.adapters.tools:http_tool_executor `
    --field-classifier agent_core.adapters.classification:field_classifier `
    --classifier testing.e2e_demo:classifier_provider --calibration testing.e2e_demo:calibration
