# Dev stack helper. Podman's data lives on D: (C: is nearly full), so every podman call
# needs XDG_DATA_HOME pointing there. This script sets it for you.
#
#   .\scripts\stack.ps1 up       # start the Podman VM + postgres, keycloak, mailpit, Ollama,
#                                # and the outbox worker (sends emails, Slack, Jira, CRM)
#   .\scripts\stack.ps1 obs      # also start Prometheus + Grafana
#   .\scripts\stack.ps1 down     # stop the worker and containers (data is kept)
#   .\scripts\stack.ps1 status
#   .\scripts\stack.ps1 stop-vm  # stop the Podman VM too (frees its RAM)

param([ValidateSet('up', 'obs', 'down', 'status', 'stop-vm')] [string]$cmd = 'status')

# Native tools (podman) write warnings to stderr; don't treat those as fatal.
$ErrorActionPreference = 'Continue'
$root = Split-Path $PSScriptRoot -Parent
$env:XDG_DATA_HOME = 'D:\PodmanData'
$env:Path = "$root\.venv\Scripts;" + [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
            [Environment]::GetEnvironmentVariable('Path', 'User')
$compose = Join-Path $root 'infra\compose.yaml'

function Start-Ollama {
    # Local model for the internal assistant; models live on D: (C: is nearly full).
    if (-not (Test-Path 'D:\Ollama\ollama.exe')) { return }
    try { Invoke-WebRequest -UseBasicParsing http://localhost:11434/api/version -TimeoutSec 2 | Out-Null; return } catch {}
    $env:OLLAMA_MODELS = 'D:\Ollama\models'
    Start-Process -FilePath 'D:\Ollama\ollama.exe' -ArgumentList 'serve' -WindowStyle Hidden
    Write-Output 'Ollama started (models in D:\Ollama\models)'
}

function Get-Worker {
    Get-CimInstance Win32_Process -Filter "Name = 'assistant-worker.exe'" -ErrorAction SilentlyContinue
}

function Start-Worker {
    # The outbox worker performs every side effect (emails, Slack, Jira tickets, CRM sync).
    # Without it, those stay queued. Logs go to logs\worker.log.
    if (Get-Worker) { Write-Output 'Worker already running'; return }
    $logs = Join-Path $root 'logs'
    New-Item -ItemType Directory -Force $logs | Out-Null
    Start-Process -FilePath (Join-Path $root '.venv\Scripts\assistant-worker.exe') `
        -WorkingDirectory (Join-Path $root 'apps\api') -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logs 'worker.log') `
        -RedirectStandardError (Join-Path $logs 'worker.err.log')
    Write-Output 'Worker started (logs\worker.log)'
}

function Stop-Worker {
    $w = Get-Worker
    if ($w) { $w | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; Write-Output 'Worker stopped' }
}

function Start-VM {
    $state = podman machine inspect --format '{{.State}}' 2>$null
    if ($state -ne 'running') { podman machine start 2>&1 | Where-Object { $_ -notmatch 'API forwarding' } }
}

switch ($cmd) {
    'up'      { Start-VM; podman-compose -f $compose up -d; Start-Ollama; Start-Worker }
    'obs'     { Start-VM; podman-compose -f $compose --profile obs up -d }
    'down'    { Stop-Worker; podman-compose -f $compose down }
    'stop-vm' { Stop-Worker; podman-compose -f $compose down; podman machine stop }
    'status'  {
        podman machine list; podman ps --format '{{.Names}}  {{.Ports}}  {{.Status}}'
        if (Get-Worker) { 'worker: running' } else { 'worker: stopped (queued emails, Slack and Jira wait)' }
    }
}
