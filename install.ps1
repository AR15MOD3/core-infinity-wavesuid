# Explicit, per-Bot installer. No background process or account operation.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$ServiceDir,
    [Parameter(Mandatory=$true)][string]$OwnerBot,
    [Parameter(Mandatory=$true)][string]$ConfigPath,
    [Parameter(Mandatory=$true)][string]$DataDir,
    [Parameter(Mandatory=$true)][int]$Port,
    [string]$PythonVersion = "3.11",
    [switch]$AllowDownloads,
    [switch]$WithPlaywright,
    [switch]$Plan
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Service = [IO.Path]::GetFullPath($ServiceDir)
$Py = Join-Path $Service ".venv\Scripts\python.exe"
$Provision = Join-Path $Root "tools\provision.py"
$ProvisionArgs = @($Provision, "--service-dir", $Service, "--config-path", $ConfigPath,
    "--owner", $OwnerBot, "--data-dir", $DataDir, "--port", "$Port", "--python", $Py)
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw "Install Python first; no automatic bootstrap." }
& python @ProvisionArgs
if ($LASTEXITCODE -ne 0) { throw "Preflight failed; nothing installed." }
if ($Plan) { exit 0 }
if (-not $AllowDownloads) { throw "Use -Plan, or explicitly pass -AllowDownloads for uv dependencies/upstream downloads." }
foreach ($Tool in @("uv", "git")) {
    if (-not (Get-Command $Tool -ErrorAction SilentlyContinue)) { throw "Install $Tool explicitly first." }
}
& python @ProvisionArgs --apply
if ($LASTEXITCODE -ne 0) { throw "Service staging failed." }
if (-not (Test-Path -LiteralPath $Py)) {
    & uv venv --python $PythonVersion (Join-Path $Service ".venv")
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed." }
}
& uv pip install --python $Py -r (Join-Path $Service "pyproject.toml")
if ($LASTEXITCODE -ne 0) { throw "Dependency install failed." }
& $Py (Join-Path $Service "tools\update_plugins.py")
if ($LASTEXITCODE -ne 0) { throw "Upstream download failed." }
if ($WithPlaywright) {
    & uv pip install --python $Py playwright
    if ($LASTEXITCODE -ne 0) { throw "Playwright dependency install failed." }
    & $Py -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw "Chromium install failed; not PNG-ready." }
}
& python @ProvisionArgs --apply --write-config
if ($LASTEXITCODE -ne 0) { throw "Scoped config preservation/creation failed." }
Write-Host "Service staged. Existing config is preserved, including its old serviceDir. Review paths explicitly before enable."
Write-Host "No Bot started, no QQ sent, no account data copied. PNG requires -WithPlaywright."
