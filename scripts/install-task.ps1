[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$TaskName = 'Libai-Liuhe'
$Root = Split-Path -Parent $PSScriptRoot
$RunScript = Join-Path $PSScriptRoot 'run-scheduled.ps1'
$Pwsh = (Get-Command pwsh.exe -ErrorAction Stop).Source
$CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

if (-not (Test-Path -LiteralPath (Join-Path $Root '.venv\Scripts\python.exe'))) {
    throw '找不到 .venv，请先运行 scripts/setup.ps1。'
}
if (-not (Test-Path -LiteralPath (Join-Path $Root '.env'))) {
    throw '找不到 .env，不能发布六合。'
}

$Action = New-ScheduledTaskAction `
    -Execute $Pwsh `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -File `"$RunScript`"" `
    -WorkingDirectory $Root
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $CurrentUser
$Principal = New-ScheduledTaskPrincipal `
    -UserId $CurrentUser `
    -LogonType Interactive `
    -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $Action `
        -Trigger $Trigger `
        -Principal $Principal `
        -Settings $Settings `
        -Description '李白六合宿主机 Agent Reach 只读网关（127.0.0.1:9204）' `
        -Force `
        -ErrorAction Stop | Out-Null

    Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    Write-Output "六合计划任务已发布并启动：$TaskName"
}
catch {
    if ($_.Exception.Message -notmatch 'Access is denied|拒绝访问') {
        throw
    }
    $StartupDirectory = [Environment]::GetFolderPath('Startup')
    $ShortcutPath = Join-Path $StartupDirectory 'Libai-Liuhe.lnk'
    $Shell = New-Object -ComObject WScript.Shell
    $Shortcut = $Shell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = $Pwsh
    $Shortcut.Arguments = "-NoProfile -NonInteractive -WindowStyle Hidden -File `"$RunScript`""
    $Shortcut.WorkingDirectory = $Root
    $Shortcut.WindowStyle = 7
    $Shortcut.Description = '李白六合宿主机 Agent Reach 只读网关'
    $Shortcut.Save()

    Start-Process `
        -FilePath $Pwsh `
        -ArgumentList @('-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-File', $RunScript) `
        -WorkingDirectory $Root `
        -WindowStyle Hidden
    Write-Output "计划任务权限不足，已改用当前用户登录启动项并启动六合：$ShortcutPath"
}
