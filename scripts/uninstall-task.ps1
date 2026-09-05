[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$TaskName = 'Libai-Liuhe'
$Removed = $false
$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $Task) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "六合计划任务已删除：$TaskName"
    $Removed = $true
}

$ShortcutPath = Join-Path ([Environment]::GetFolderPath('Startup')) 'Libai-Liuhe.lnk'
if (Test-Path -LiteralPath $ShortcutPath) {
    Remove-Item -LiteralPath $ShortcutPath -Force
    Write-Output "六合登录启动项已删除：$ShortcutPath"
    $Removed = $true
}

if (-not $Removed) {
    Write-Output '六合没有已注册的自动启动项。'
}
