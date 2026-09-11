# Stop whatever listens on the dashboard port. Usage: .\stop.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$port = 3838
if (Test-Path config\profile.yaml) {
    $m = Select-String -Path config\profile.yaml -Pattern '^\s*port:\s*(\d+)' | Select-Object -First 1
    if ($m) { $port = [int]$m.Matches[0].Groups[1].Value }
}
$conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if (-not $conns) { Write-Host "nothing listening on :$port"; Remove-Item data\server.pid -ErrorAction SilentlyContinue; exit 0 }
$pids = $conns | Select-Object -ExpandProperty OwningProcess -Unique
foreach ($procId in $pids) { Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue }
Remove-Item data\server.pid -ErrorAction SilentlyContinue
Write-Host "stopped ($($pids -join ', '))"
