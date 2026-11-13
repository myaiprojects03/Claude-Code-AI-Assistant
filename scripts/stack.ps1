# Dev stack helper. Podman's data lives on D: (C: is nearly full), so every podman call
# needs XDG_DATA_HOME pointing there. This script sets it for you.
#
#   .\scripts\stack.ps1 up       # start the Podman VM + postgres, keycloak, mailpit
#   .\scripts\stack.ps1 obs      # also start Prometheus + Grafana
#   .\scripts\stack.ps1 down     # stop containers (data is kept)
#   .\scripts\stack.ps1 status
#   .\scripts\stack.ps1 stop-vm  # stop the Podman VM too (frees its RAM)

param([ValidateSet('up', 'obs', 'down', 'status', 'stop-vm')] [string]$cmd = 'status')

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$env:XDG_DATA_HOME = 'D:\PodmanData'
$env:Path = "$root\.venv\Scripts;" + [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
            [Environment]::GetEnvironmentVariable('Path', 'User')
$compose = Join-Path $root 'infra\compose.yaml'

function Start-VM {
    $state = podman machine inspect --format '{{.State}}' 2>$null
    if ($state -ne 'running') { podman machine start }
}

switch ($cmd) {
    'up'      { Start-VM; podman-compose -f $compose up -d }
    'obs'     { Start-VM; podman-compose -f $compose --profile obs up -d }
    'down'    { podman-compose -f $compose down }
    'stop-vm' { podman-compose -f $compose down; podman machine stop }
    'status'  { podman machine list; podman ps --format '{{.Names}}  {{.Ports}}  {{.Status}}' }
}
