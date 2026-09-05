[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Venv = Join-Path $Root '.venv'
$Python = Join-Path $Venv 'Scripts\python.exe'
$Uv = Get-Command uv.exe -ErrorAction SilentlyContinue

if (-not (Test-Path -LiteralPath $Python)) {
    if ($null -ne $Uv) {
        $env:UV_CACHE_DIR = Join-Path $Root '.uv-cache'
        & $Uv.Source venv --python 3.13 $Venv
    }
    else {
        $Launcher = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($null -eq $Launcher) {
            throw '找不到 uv 或 Python Launcher，请先安装 Python 3.13。'
        }
        & $Launcher.Source -3.13 -m venv $Venv
    }
    if ($LASTEXITCODE -ne 0) { throw '创建六合 Python 虚拟环境失败。' }
}

if ($null -ne $Uv) {
    $env:UV_CACHE_DIR = Join-Path $Root '.uv-cache'
    & $Uv.Source pip install --python $Python -e "${Root}[test]"
}
else {
    $env:PIP_CACHE_DIR = Join-Path $Root '.pip-cache'
    & $Python -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw '升级 pip 失败。' }
    & $Python -m pip install -e "${Root}[test]"
}
if ($LASTEXITCODE -ne 0) { throw '安装六合依赖失败。' }

Write-Output '六合运行环境已就绪。'
