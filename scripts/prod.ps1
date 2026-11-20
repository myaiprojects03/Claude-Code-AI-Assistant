# Production (public demo) on this PC, Phase 8. Runs beside the dev stack with its own
# database, ports and settings (.env.prod). Tailscale Funnel makes it public.
#
#   .\scripts\prod.ps1 init          # create .env.prod with fresh random secrets (once)
#   .\scripts\prod.ps1 build         # build the dashboard + website demo for PUBLIC_URL
#   .\scripts\prod.ps1 up            # containers, Keycloak sync, migrations, API, worker, Caddy
#   .\scripts\prod.ps1 load-data     # first time: public + internal content, demo data
#   .\scripts\prod.ps1 status
#   .\scripts\prod.ps1 down          # stop API, worker, Caddy and containers (data kept)
#   .\scripts\prod.ps1 backup        # dump both databases to D:\Backups\assistant (keeps 14)
#   .\scripts\prod.ps1 restore-test  # restore drill: newest backup into a scratch database
#   .\scripts\prod.ps1 funnel-on     # publish through Tailscale Funnel (443 and 8443)
#   .\scripts\prod.ps1 funnel-off

param(
    [ValidateSet('init', 'build', 'up', 'load-data', 'status', 'down', 'backup', 'restore-test',
                 'sync-keycloak', 'funnel-on', 'funnel-off')]
    [string]$cmd = 'status'
)

$ErrorActionPreference = 'Continue'
$root = Split-Path $PSScriptRoot -Parent
$envFile = Join-Path $root '.env.prod'
$compose = Join-Path $root 'infra\prod\compose.yaml'
$caddyfile = Join-Path $root 'infra\prod\Caddyfile'
$caddy = 'D:\Caddy\caddy.exe'
$py = Join-Path $root '.venv\Scripts\python.exe'
$api = Join-Path $root 'apps\api'
$logs = Join-Path $root 'logs'
$pidFile = Join-Path $logs 'prod.pids.json'
$deploy = Join-Path $root 'deploy'
$backups = 'D:\Backups\assistant'
$pg = 'assistant-prod_postgres_1'
$env:XDG_DATA_HOME = 'D:\PodmanData'   # Podman's data is on D: (C: is nearly full)
$env:Path = "$root\.venv\Scripts;" + [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
            [Environment]::GetEnvironmentVariable('Path', 'User')

function New-Secret([int]$n = 40) {
    -join ((48..57) + (65..90) + (97..122) | Get-Random -Count $n | ForEach-Object { [char]$_ })
}

function Read-EnvProd {
    if (-not (Test-Path $envFile)) { throw ".env.prod is missing: run .\scripts\prod.ps1 init" }
    $cfg = @{}
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*([A-Z0-9_]+)\s*=\s*(.*)$') { $cfg[$Matches[1]] = $Matches[2].Trim() }
    }
    foreach ($k in 'PUBLIC_URL', 'DASHBOARD_URL', 'APP_SECRET', 'POSTGRES_PASSWORD', 'KC_ADMIN_PASSWORD') {
        if (-not $cfg[$k]) { throw ".env.prod: $k is empty" }
    }
    $cfg['PUBLIC_URL'] = $cfg['PUBLIC_URL'].TrimEnd('/')
    $cfg['DASHBOARD_URL'] = $cfg['DASHBOARD_URL'].TrimEnd('/')
    if (-not $cfg['API_PORT']) { $cfg['API_PORT'] = '8001' }
    $cfg
}

function Set-ProdEnv($cfg) {
    # Values derived from the addresses; process variables win over the env file.
    $u = [Uri]$cfg['PUBLIC_URL']
    $env:ASSISTANT_ENV_FILE = $envFile
    $env:DATABASE_URL = "postgresql+asyncpg://assistant:$($cfg['POSTGRES_PASSWORD'])@localhost:5434/assistant"
    $env:OIDC_ISSUER = "$($cfg['PUBLIC_URL'])/auth/realms/assistant"
    $env:OIDC_JWKS_URL = 'http://127.0.0.1:8180/auth/realms/assistant/protocol/openid-connect/certs'
    $env:ALLOWED_ORIGINS = "$($cfg['PUBLIC_URL']),$($cfg['DASHBOARD_URL'])"
    $env:DASHBOARD_URL = $cfg['DASHBOARD_URL']
    $env:API_PORT = $cfg['API_PORT']
    $env:PUBLIC_SCHEME = $u.Scheme
    $env:PUBLIC_HOST = $u.Authority
    $env:SITE_DIR = Join-Path $deploy 'site'
    $env:DASH_DIR = Join-Path $deploy 'dashboard'
}

function Get-Pids {
    if (Test-Path $pidFile) { Get-Content $pidFile -Raw | ConvertFrom-Json } else { $null }
}

function Test-Pid($id) { $id -and (Get-Process -Id $id -ErrorAction SilentlyContinue) }

function Start-VM {
    $state = podman machine inspect --format '{{.State}}' 2>$null
    if ($state -ne 'running') { podman machine start 2>&1 | Where-Object { $_ -notmatch 'API forwarding' } }
}

function Start-Ollama {
    if (-not (Test-Path 'D:\Ollama\ollama.exe')) { return }
    try { Invoke-WebRequest -UseBasicParsing http://localhost:11434/api/version -TimeoutSec 2 | Out-Null; return } catch {}
    $env:OLLAMA_MODELS = 'D:\Ollama\models'
    Start-Process -FilePath 'D:\Ollama\ollama.exe' -ArgumentList 'serve' -WindowStyle Hidden
    Write-Output 'Ollama started'
}

function Wait-Url($url, [int]$seconds = 180) {
    $until = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $until) {
        try { Invoke-WebRequest -UseBasicParsing $url -TimeoutSec 3 | Out-Null; return $true } catch { Start-Sleep 3 }
    }
    $false
}

function Sync-Keycloak($cfg) {
    # The realm is imported once; addresses and demo passwords are set here on every start, so
    # changing PUBLIC_URL / DASHBOARD_URL or a password in .env.prod needs no re-import.
    $kc = 'http://127.0.0.1:8180/auth'
    $tok = (Invoke-RestMethod -Method Post "$kc/realms/master/protocol/openid-connect/token" -Body @{
        grant_type = 'password'; client_id = 'admin-cli'; username = 'admin'; password = $cfg['KC_ADMIN_PASSWORD']
    }).access_token
    $h = @{ Authorization = "Bearer $tok" }
    $admin = "$kc/admin/realms/assistant"
    $client = (Invoke-RestMethod -Headers $h "$admin/clients?clientId=assistant-dashboard")[0]
    $client.redirectUris = @("$($cfg['DASHBOARD_URL'])/*")
    $client.webOrigins = @($cfg['DASHBOARD_URL'])
    Invoke-RestMethod -Method Put -Headers $h -ContentType 'application/json' "$admin/clients/$($client.id)" `
        -Body ($client | ConvertTo-Json -Depth 20) | Out-Null
    foreach ($pair in @(@('admin.dev', 'DEMO_ADMIN_PASSWORD'), @('manager.dev', 'DEMO_MANAGER_PASSWORD'),
                        @('consultant.dev', 'DEMO_CONSULTANT_PASSWORD'))) {
        $pw = $cfg[$pair[1]]
        if (-not $pw) { Write-Output "  $($pair[0]): no password in .env.prod, sign-in disabled"; continue }
        $user = (Invoke-RestMethod -Headers $h "$admin/users?username=$($pair[0])&exact=true")[0]
        Invoke-RestMethod -Method Put -Headers $h -ContentType 'application/json' `
            "$admin/users/$($user.id)/reset-password" `
            -Body (@{ type = 'password'; value = $pw; temporary = $false } | ConvertTo-Json) | Out-Null
    }
    Write-Output "Keycloak: dashboard address $($cfg['DASHBOARD_URL']), demo passwords set"
}

function Start-Hidden($name, $file, $argList, $workDir) {
    New-Item -ItemType Directory -Force $logs | Out-Null
    $p = Start-Process -PassThru -FilePath $file -ArgumentList $argList -WorkingDirectory $workDir `
        -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs "prod-$name.log") `
        -RedirectStandardError (Join-Path $logs "prod-$name.err.log")
    $p.Id
}

function Start-App($cfg) {
    $pids = Get-Pids
    $out = @{}
    foreach ($n in 'api', 'worker', 'caddy') { if ($pids -and (Test-Pid $pids.$n)) { $out[$n] = $pids.$n } }
    if (-not $out['api']) {
        $out['api'] = Start-Hidden 'api' $py @('-m', 'uvicorn', 'assistant.api.main:app', '--host', '127.0.0.1',
                                                '--port', $cfg['API_PORT'], '--no-access-log') $api
    }
    if (-not $out['worker']) {
        $out['worker'] = Start-Hidden 'worker' $py @('-c', '"from assistant.outbox.worker import main; main()"') $api
    }
    if (-not $out['caddy']) {
        $out['caddy'] = Start-Hidden 'caddy' $caddy @('run', '--config', "`"$caddyfile`"", '--adapter', 'caddyfile') $root
    }
    $out | ConvertTo-Json | Set-Content -Encoding utf8 $pidFile
    if (Wait-Url "http://127.0.0.1:8081/readyz" 90) { Write-Output "Up: $($cfg['PUBLIC_URL'])  dashboard: $($cfg['DASHBOARD_URL'])" }
    else { Write-Output 'API did not become ready: see logs\prod-api.err.log' }
}

function Stop-App {
    $pids = Get-Pids
    if (-not $pids) { return }
    foreach ($n in 'caddy', 'api', 'worker') {
        if (Test-Pid $pids.$n) { Stop-Process -Id $pids.$n -Force; Write-Output "$n stopped" }
    }
    Remove-Item $pidFile
}

function Invoke-Api([string[]]$a) { Push-Location $api; & $py @a; Pop-Location }

switch ($cmd) {
    'init' {
        if (Test-Path $envFile) { Write-Output '.env.prod already exists; not touching it'; break }
        $text = Get-Content (Join-Path $root '.env.prod.example') -Raw
        foreach ($k in 'APP_SECRET', 'POSTGRES_PASSWORD', 'KC_ADMIN_PASSWORD', 'DEMO_ADMIN_PASSWORD',
                       'DEMO_MANAGER_PASSWORD', 'DEMO_CONSULTANT_PASSWORD') {
            $len = if ($k -eq 'APP_SECRET') { 48 } elseif ($k -like 'DEMO_*') { 16 } else { 32 }
            $text = $text -replace "(?m)^$k=\s*$", "$k=$(New-Secret $len)"
        }
        [IO.File]::WriteAllText($envFile, $text)
        Write-Output 'Created .env.prod with new secrets. Paste GROQ_API_KEY (and GEMINI_API_KEY) into it yourself.'
    }
    'build' {
        $cfg = Read-EnvProd
        Push-Location (Join-Path $root 'apps\widget'); npm run build | Select-Object -Last 3; Pop-Location
        $site = Join-Path $deploy 'site'
        New-Item -ItemType Directory -Force $site | Out-Null
        Copy-Item (Join-Path $root 'apps\widget\dist\assistant-chat.js') $site -Force
        Copy-Item (Join-Path $root 'apps\widget\demo\favicon.svg') $site -Force
        $demo = [IO.File]::ReadAllText((Join-Path $root 'apps\widget\demo\index.html'), [Text.Encoding]::UTF8)
        $tag = '<script type="module" src="/src/main.jsx" data-api="http://localhost:8000"></script>'
        if (-not $demo.Contains($tag)) { throw 'demo/index.html: widget script tag not found' }
        $demo = $demo.Replace($tag, "<script src=`"/assistant-chat.js`" data-api=`"$($cfg['PUBLIC_URL'])`" defer></script>")
        [IO.File]::WriteAllText((Join-Path $site 'index.html'), $demo)
        $env:VITE_API_URL = $cfg['PUBLIC_URL']
        $env:VITE_OIDC_ISSUER = "$($cfg['PUBLIC_URL'])/auth/realms/assistant"
        Push-Location (Join-Path $root 'apps\web')
        npx vite build --outDir (Join-Path $deploy 'dashboard') --emptyOutDir | Select-Object -Last 3
        Pop-Location
        Write-Output "Built deploy\site and deploy\dashboard for $($cfg['PUBLIC_URL'])"
    }
    'up' {
        $cfg = Read-EnvProd; Set-ProdEnv $cfg
        if (-not (Test-Path (Join-Path $deploy 'dashboard\index.html'))) { throw 'Run .\scripts\prod.ps1 build first' }
        Start-VM
        podman-compose --env-file $envFile -f $compose up -d 2>&1 | Select-Object -Last 3
        Write-Output 'Waiting for Keycloak (production mode starts slowly the first time)...'
        if (-not (Wait-Url 'http://127.0.0.1:8180/auth/realms/assistant/.well-known/openid-configuration' 300)) {
            throw 'Keycloak did not start: podman logs assistant-prod_keycloak_1'
        }
        Sync-Keycloak $cfg
        Invoke-Api @('-m', 'alembic', 'upgrade', 'head')
        Start-Ollama
        Start-App $cfg
    }
    'load-data' {
        $cfg = Read-EnvProd; Set-ProdEnv $cfg
        Invoke-Api @('-m', 'alembic', 'upgrade', 'head')
        Push-Location $api
        assistant-seed --demo; assistant-ingest; assistant-ingest-internal
        Pop-Location
    }
    'sync-keycloak' { $cfg = Read-EnvProd; Sync-Keycloak $cfg }
    'status' {
        podman ps --filter name=assistant-prod --format '{{.Names}}  {{.Status}}'
        $pids = Get-Pids
        foreach ($n in 'api', 'worker', 'caddy') {
            if ($pids -and (Test-Pid $pids.$n)) { "${n}: running (pid $($pids.$n))" } else { "${n}: stopped" }
        }
        try { 'readyz: ' + (Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8081/readyz -TimeoutSec 3).StatusCode } catch { 'readyz: not answering' }
    }
    'down' {
        Stop-App
        podman-compose --env-file $envFile -f $compose down 2>&1 | Select-Object -Last 2
    }
    'backup' {
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $dir = Join-Path $backups $stamp
        New-Item -ItemType Directory -Force $dir | Out-Null
        foreach ($db in 'assistant', 'keycloak') {
            # Dump inside the container, then copy out (PowerShell pipes would corrupt binary).
            podman exec $pg pg_dump -U assistant -Fc -f "/tmp/$db.dump" $db
            if ($LASTEXITCODE -ne 0) { throw "pg_dump $db failed" }
            podman cp "${pg}:/tmp/$db.dump" (Join-Path $dir "$db.dump")
            podman exec $pg rm -f "/tmp/$db.dump"
        }
        Get-ChildItem $backups -Directory | Sort-Object Name -Descending | Select-Object -Skip 14 |
            Remove-Item -Recurse -Force
        Get-ChildItem $dir | ForEach-Object { '{0}  {1:N1} MB' -f $_.FullName, ($_.Length / 1MB) }
    }
    'restore-test' {
        # Restore drill: the newest backup into a scratch database, compare row counts, drop it.
        $dir = Get-ChildItem $backups -Directory | Sort-Object Name -Descending | Select-Object -First 1
        if (-not $dir) { throw "No backups in $backups" }
        podman cp (Join-Path $dir.FullName 'assistant.dump') "${pg}:/tmp/restore.dump"
        podman exec $pg psql -U assistant -d postgres -q -c 'DROP DATABASE IF EXISTS restore_check' -c 'CREATE DATABASE restore_check'
        podman exec $pg pg_restore -U assistant -d restore_check --no-owner /tmp/restore.dump
        $q = "SELECT 'conversations', count(*) FROM conversations UNION ALL SELECT 'messages', count(*) FROM messages UNION ALL SELECT 'bookings', count(*) FROM bookings UNION ALL SELECT 'kb_public_chunks', count(*) FROM kb_public_chunks UNION ALL SELECT 'audit_log', count(*) FROM audit_log"
        Write-Output "Backup $($dir.Name), restored copy vs live:"
        $restored = podman exec $pg psql -U assistant -d restore_check -At -F ' ' -c $q
        $live = podman exec $pg psql -U assistant -d assistant -At -F ' ' -c $q
        for ($i = 0; $i -lt $restored.Count; $i++) { "  restored: $($restored[$i])   live now: $(($live[$i] -split ' ')[1])" }
        podman exec $pg psql -U assistant -d postgres -q -c 'DROP DATABASE restore_check'
        podman exec $pg rm -f /tmp/restore.dump
    }
    'funnel-on' {
        tailscale funnel --bg --https=443 http://127.0.0.1:8081
        tailscale funnel --bg --https=8443 http://127.0.0.1:8082
        tailscale funnel status
    }
    'funnel-off' { tailscale funnel reset; Write-Output 'Funnel off: nothing is public' }
}
