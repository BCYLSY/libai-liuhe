[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $Root '.env'
$Python = Join-Path $Root '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $Python)) {
    throw '找不到 .venv，先运行 scripts/setup.ps1。'
}
if (-not (Test-Path -LiteralPath $EnvFile)) {
    throw '缺少 .env，请复制 .env.example 并替换调用方 Token。'
}

function Get-EnvSetting([string]$Name, [string]$DefaultValue) {
    $pattern = "^{0}=(.*)$" -f [regex]::Escape($Name)
    $line = Get-Content -LiteralPath $EnvFile |
        Where-Object { $_ -match $pattern } |
        Select-Object -Last 1
    if ($null -eq $line) { return $DefaultValue }
    return ($line -split '=', 2)[1].Trim()
}

$BindAddress = Get-EnvSetting 'LIUHE_BIND_ADDRESS' '127.0.0.1'
$Port = [int](Get-EnvSetting 'LIUHE_PORT' '9204')
$LogLevel = Get-EnvSetting 'LIUHE_LOG_LEVEL' 'info'
$occupied = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalAddress -eq $BindAddress -or $_.LocalAddress -in @('0.0.0.0', '::') }
if ($occupied) {
    throw "${BindAddress}:$Port 已被占用；六合不会停止或覆盖现有服务。"
}

$localBin = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.local\bin'
if (Test-Path -LiteralPath $localBin) {
    $env:PATH = "$localBin;$($env:PATH)"
}
$env:PYTHONUTF8 = '1'

Push-Location $Root
try {
    & $Python -m uvicorn app.main:app --host $BindAddress --port $Port --log-level $LogLevel
    if ($LASTEXITCODE -ne 0) { throw '六合网关异常退出。' }
}
finally {
    Pop-Location
}

