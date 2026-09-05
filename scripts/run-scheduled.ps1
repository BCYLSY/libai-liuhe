[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$LogPath = Join-Path $Root 'liuhe.service.log'
$StartScript = Join-Path $PSScriptRoot 'start-api.ps1'

& $StartScript *>> $LogPath

