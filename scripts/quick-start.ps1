# JLU Booking 快速部署（Windows PowerShell）
#
#   irm https://raw.githubusercontent.com/xiaoyoumi9924/jlu-booking/main/scripts/quick-start.ps1 | iex
#
# 第一次运行：下载仓库 -> 部署 -> 打开页面。
# 之后运行：更新仓库 -> 直接打开页面。
#
# 可选环境变量：
#   JLU_BOOKING_HOME       安装目录（默认 %USERPROFILE%\.jlu-booking）
#   JLU_BOOKING_BRANCH     分支（默认 main）
#   JLU_BOOKING_REPO_URL   仓库地址（默认 GitHub）
#   JLU_BOOKING_DRY_RUN=1  只下载/更新，不启动

$ErrorActionPreference = "Stop"

$RepoUrl = if ($env:JLU_BOOKING_REPO_URL) { $env:JLU_BOOKING_REPO_URL } else { "https://github.com/xiaoyoumi9924/jlu-booking.git" }
$Branch = if ($env:JLU_BOOKING_BRANCH) { $env:JLU_BOOKING_BRANCH } else { "main" }
$Target = if ($env:JLU_BOOKING_HOME) { $env:JLU_BOOKING_HOME } else { Join-Path $HOME ".jlu-booking" }
$Fresh = $false

function Fail([string]$Message) {
    Write-Host "错误：$Message" -ForegroundColor Red
    exit 1
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Fail "未找到 git。请先安装 Git for Windows：https://git-scm.com/download/win"
}

$Python = $null
$PythonArgs = @()
if (Get-Command python -ErrorAction SilentlyContinue) {
    $Python = "python"
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $Python = "py"
    $PythonArgs = @("-3")
} else {
    Fail "未找到 Python。请先安装 Python 3.10 或更高版本（安装时勾选 Add Python to PATH）。"
}

& $Python @PythonArgs -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
if ($LASTEXITCODE -ne 0) {
    Fail "Python 版本过低，需要 3.10 或更高版本。"
}

if (Test-Path (Join-Path $Target ".git")) {
    Write-Host "更新已有安装：$Target"
    git -C $Target pull --ff-only --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Host "（更新失败，使用本地版本继续）"
    }
} elseif (Test-Path $Target) {
    Fail "$Target 已存在但不是 Git 仓库。请移走它，或设置 JLU_BOOKING_HOME 指向其他目录。"
} else {
    Write-Host "下载 JLU Booking 到 $Target"
    git clone --quiet --depth 1 --branch $Branch $RepoUrl $Target
    if ($LASTEXITCODE -ne 0) {
        Fail "下载失败，请检查网络后重试。"
    }
    $Fresh = $true
}

Set-Location $Target

if ($env:JLU_BOOKING_DRY_RUN -eq "1") {
    Write-Host "已就绪：${Target}（dry-run，未启动）"
    exit 0
}

if ($Fresh) {
    Write-Host "以后打开：重新运行本命令，或双击 $Target\deploy.bat"
}

& $Python @PythonArgs "tools\deploy_local.py" open
exit $LASTEXITCODE
