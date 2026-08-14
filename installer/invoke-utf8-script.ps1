param(
    [Parameter(Mandatory = $true)]
    [string]$Path
)

$ErrorActionPreference = "Stop"
$Utf8 = New-Object System.Text.UTF8Encoding($false)
$ScriptText = [System.IO.File]::ReadAllText($Path, $Utf8)
& ([scriptblock]::Create($ScriptText))
