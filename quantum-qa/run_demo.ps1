param(
    [string]$Url = "https://stg.gajab.com/",
    [ValidateSet("chromium", "firefox", "webkit")]
    [string]$Browser = "chromium",
    [switch]$Headed = $true
)

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$args = @(
    "quantum-qa/ui_flow.py",
    "--ui-url", $Url,
    "--ui-browser", $Browser,
    "--ui-problem-statement-file", "quantum-qa/Problemstatement/workflow_gajab_demo.txt",
    "--ui-mode", "strict",
    "--run-profile", "demo",
    "--step-pause-ms", "900",
    "--clean-run-data"
)

if ($Headed) {
    $args += "--headed"
}

python @args
