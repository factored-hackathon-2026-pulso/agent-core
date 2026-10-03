<# Métricas por agente desde la auditoría. Ej: .\scripts\e2e\report.ps1 ; -Agent copiloto-asesor ; -Run <run_id> ; -Json #>
param([string]$Agent, [string]$Run, [switch]$Json)
. $PSScriptRoot\_env.ps1
Set-Location $Root
Import-E2EEnv
$extra = @()
if ($Agent) { $extra += @("--agent", $Agent) }
if ($Run) { $extra += @("--run", $Run) }
if ($Json) { $extra += "--json" }
uv run python scripts/e2e/report.py @extra
