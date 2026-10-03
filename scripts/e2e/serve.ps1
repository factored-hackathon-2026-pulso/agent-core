<# Arranca `agentcore serve` (demo local, con la API del registry) en 127.0.0.1:8000. Déjalo abierto. #>
. $PSScriptRoot\_env.ps1
Set-Location $Root
Import-E2EEnv
if (-not $env:AGENTCORE_JEV_API_KEY) { throw "Falta AGENTCORE_JEV_API_KEY en $EnvFile (serve la exige)." }
uv run agentcore serve `
    --identity-keys "$StateDir\identity-keys.json" --staff-keys "$StateDir\staff-keys.json" `
    --registry-api `
    --agents "recepcion,disputas,consultas,copiloto-asesor,constructor-chat" `
    --tools testing.e2e_demo:tools --classifier testing.e2e_demo:classifier_provider `
    --field-classifier testing.e2e_demo:field_classifier --calibration testing.e2e_demo:calibration
