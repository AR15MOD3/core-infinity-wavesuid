# Install the waves service for core-infinity-wavesuid (Windows).
#
#   powershell -ExecutionPolicy Bypass -File .\install.ps1
#   powershell -ExecutionPolicy Bypass -File .\install.ps1 -WithPlaywright
#
# What it does:
#   1. makes sure uv + git exist
#   2. creates service\.venv (Python 3.10-3.13)
#   3. installs service dependencies
#   4. clones XutheringWavesUID / RoverSign / ScoreEcho into service\plugins
#   5. writes the plugin config.json (paths + autoStart) if you don't have one
#   6. optionally installs Playwright + Chromium (only needed for PNG cards)
#
# It never touches your game account, and never starts anything in the background.

[CmdletBinding()]
param(
    [string]$PythonVersion = "3.11",
    [switch]$WithPlaywright,
    [switch]$SkipPluginConfig
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Service = Join-Path $Root "service"
$Venv = Join-Path $Service ".venv"
$Py = Join-Path $Venv "Scripts\python.exe"

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host $text -ForegroundColor Red; exit 1 }

if (-not (Test-Path (Join-Path $Service "rover\main.py"))) {
    Fail "找不到 $Service\rover\main.py —— 请把 install.ps1 放在插件仓库根目录运行。"
}

# ---- 1. uv + git ------------------------------------------------------------
Step "检查 uv 与 git"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "没有 uv，尝试安装（官方脚本）..."
    try {
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
        $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    } catch {
        Fail "uv 安装失败。请手工装：powershell -c ""irm https://astral.sh/uv/install.ps1 | iex"""
    }
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Fail "uv 仍然不可用，请重开一个终端再试。" }
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Fail "缺少 git。请先安装 Git for Windows（https://git-scm.com/download/win），再重跑本脚本。"
}
Write-Host ("uv " + (uv --version))

# ---- 2. venv ----------------------------------------------------------------
Step "创建虚拟环境（Python $PythonVersion）"
if (-not (Test-Path $Py)) {
    uv venv --python $PythonVersion $Venv
    if ($LASTEXITCODE -ne 0) { Fail "虚拟环境创建失败。可试：uv python install $PythonVersion" }
}
& $Py -c "import sys; print('python', sys.version.split()[0])"

# ---- 3. dependencies --------------------------------------------------------
Step "安装服务依赖（第一次要下载几百 MB，耐心等）"
uv pip install --python $Py -r (Join-Path $Service "pyproject.toml")
if ($LASTEXITCODE -ne 0) { Fail "依赖安装失败，见上面的输出。" }

# ---- 4. upstream plugins ----------------------------------------------------
Step "拉取上游插件（XutheringWavesUID / RoverSign / ScoreEcho）"
& $Py (Join-Path $Service "tools\update_plugins.py")
if ($LASTEXITCODE -ne 0) { Fail "上游插件拉取失败（网络或 GitHub 限流）。稍后重跑本脚本即可续上。" }

# ---- 5. optional playwright -------------------------------------------------
if ($WithPlaywright) {
    Step "安装 Playwright 与 Chromium（只有 PNG 卡片需要）"
    uv pip install --python $Py playwright
    & $Py -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { Write-Host "Playwright 安装失败；不影响文字与 HTML 卡片。" -ForegroundColor Yellow }
}

# ---- 6. plugin config -------------------------------------------------------
if (-not $SkipPluginConfig) {
    $Config = Join-Path $Root "config.json"
    if (Test-Path $Config) {
        Step "已有 config.json，未改动"
        Write-Host "  python     = $Py"
        Write-Host "  serviceDir = $Service"
    } else {
        Step "写入插件 config.json"
        $example = Get-Content (Join-Path $Root "config.example.json") -Raw | ConvertFrom-Json
        $example.python = $Py
        $example.serviceDir = $Service
        $example.autoStart = $true
        $example | ConvertTo-Json -Depth 5 | Set-Content -Path $Config -Encoding UTF8
        Write-Host "  已写入 $Config（autoStart = true）"
    }
}

Step "完成"
$summary = @"
插件配置（Config 页面里也能看到）：
  python      = $Py
  serviceDir  = $Service
  autoStart   = true
  mediaFormat = html    （要 PNG 卡片就跑一次 -WithPlaywright 再改成 png）

下一步：在 TelyinCore 的插件页启用「鸣潮查询」，调一次 waves_status 看是否在线。
卸载：停用插件 → 删掉 $Service 整个目录即可（账号数据在 service\data 里）。
"@
Write-Host $summary -ForegroundColor Green
