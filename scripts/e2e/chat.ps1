<#
 Conversa con un agente. Ejemplos:
   .\scripts\e2e\chat.ps1 -As customer   -Agent recepcion            # prueba A: resolver un problema
   .\scripts\e2e\chat.ps1 -As advisor    -Agent copiloto-asesor      # prueba B: asesorar a un cliente
   .\scripts\e2e\chat.ps1 -As supervisor -Agent constructor-chat     # prueba C: agente constructor
 Dentro del chat: /run (estado), /transcript, /quit.
#>
param(
    [ValidateSet("customer", "advisor", "supervisor")] [string]$As = "customer",
    [Parameter(Mandatory)] [string]$Agent,
    [string]$Customer = "cust-001",
    [ValidateSet("es", "pt", "")] [string]$Lang = ""
)
. $PSScriptRoot\_env.ps1
Set-Location $Root
$langArgs = @()
if ($Lang) { $langArgs = @("--lang", $Lang) }
uv run python -m testing.chat --as $As --agent $Agent --customer $Customer @langArgs
