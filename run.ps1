# Start the jobfinder dashboard + scheduler on Windows (single instance). Usage: .\run.ps1 [-NoOpen]
param([switch]$NoOpen)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$port = 3838
if (Test-Path config\profile.yaml) {
    $m = Select-String -Path config\profile.yaml -Pattern '^\s*port:\s*(\d+)' | Select-Object -First 1
    if ($m) { $port = [int]$m.Matches[0].Groups[1].Value }
}
New-Item -ItemType Directory -Force -Path data\logs | Out-Null
if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "jobfinder already listening on :$port"
    if (-not $NoOpen) { Start-Process "http://localhost:$port" }
    exit 0
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Write-Error "uv not found — https://docs.astral.sh/uv/"; exit 1 }
uv sync --quiet
uv run jobfinder db upgrade
$p = Start-Process -FilePath "uv" -ArgumentList "run", "jobfinder", "serve" -WorkingDirectory $PSScriptRoot `
    -RedirectStandardOutput data\logs\server.log -RedirectStandardError data\logs\server.err.log -WindowStyle Hidden -PassThru
$p.Id | Set-Content data\server.pid
$up = $false
for ($i = 0; $i -lt 30 -and -not $up; $i++) {
    Start-Sleep -Seconds 1
    $up = [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}
if ($up) {
    Write-Host "jobfinder up on http://localhost:$port (pid $($p.Id))"
    if (-not $NoOpen) { Start-Process "http://localhost:$port" }
} else {
    Write-Host "failed to start; see data\logs\server.err.log"; Get-Content data\logs\server.err.log -Tail 20; exit 1
}
