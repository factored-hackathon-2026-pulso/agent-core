<# Apaga el stack. -Wipe además borra los datos de Postgres. #>
param([switch]$Wipe)
. $PSScriptRoot\_env.ps1
Set-Location $Root
docker rm -f llm-gateway-e2e *> $null
if ($Wipe) { Dc down -v } else { Dc down }
