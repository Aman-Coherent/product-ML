# Runs automatically before `npm run dev` (see the "predev" npm script).
# Three jobs:
#   1. Free ports 8000 (backend) and 3000 (frontend) if a previous run left
#      a zombie process behind - this project has repeatedly hit "address
#      already in use" from stale uvicorn/next processes surviving a
#      terminal being closed instead of the app being stopped cleanly.
#   2. Kill any orphaned dev-stack process from a previous `npm run dev`
#      session - the ARQ worker, the backend, the frontend, AND the
#      scripts/dev.mjs orchestrator itself (see dev.mjs's own docstring for
#      the cmd.exe "Terminate batch job?" story that motivated it). Matched
#      by command line, not by port, because:
#        - the worker binds no port at all, so Free-Port above never
#          touches it;
#        - and even for the backend/frontend, killing *only* the process
#          that happens to hold the listening socket leaves every process
#          ABOVE it in the tree (the .venv shim, dev.mjs, the cmd.exe that
#          launched it, npm's own node process) still running - confirmed
#          in practice: stopping a dev session via something that kills
#          just the top-level tracked process (no cascading signal to
#          children at all, unlike a real terminal Ctrl+C) orphaned the
#          *entire* tree, not just the listening leaf. Since Python doesn't
#          hot-reload, any of these surviving is silently running whatever
#          OLD code was loaded when it started, out of sync with the
#          current source, and (for the worker) still polling the same
#          shared Redis queue the new session's worker also polls.
#   3. Make sure Redis is up on 6379, starting it detached if needed. Redis
#      is deliberately NOT managed by dev.mjs: it holds job-queue/checkpoint
#      state that must outlive individual dev sessions, so we only ever
#      start it here, never kill it here.

function Free-Port($port) {
    $lines = netstat -ano | Select-String ":$port " | Select-String "LISTENING"
    if (-not $lines) { return }
    $pids = $lines | ForEach-Object { ($_ -split '\s+')[-1] } | Sort-Object -Unique
    foreach ($p in $pids) {
        Write-Host "[predev] port $port is in use by pid $p - stopping it"
        taskkill /F /T /PID $p 2>$null | Out-Null
    }
}

function Kill-OrphanedDevProcesses {
    # The python patterns (arq_worker/uvicorn backend.main) are specific
    # enough to this project's own module names to not need path-scoping.
    # The node "next dev" pattern is NOT specific enough on its own - this
    # machine runs other, unrelated Next.js projects (e.g. NutraIQX-11-dev)
    # whose own `next dev` process would otherwise false-positive-match and
    # get killed. dev.mjs always passes an ABSOLUTE path to Next's bin
    # script, so requiring this project's own root in the command line
    # scopes it correctly.
    $projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    $patterns = @(
        "arq\s+backend\.workers\.arq_worker",
        "uvicorn\s+backend\.main:app",
        [regex]::Escape($projectRoot) + ".*scripts[\\/]dev\.mjs",
        [regex]::Escape($projectRoot) + ".*next[\\/]dist[\\/]bin[\\/]next"
    )
    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='node.exe'" |
        Where-Object {
            $cmd = $_.CommandLine
            $cmd -and ($patterns | Where-Object { $cmd -match $_ })
        }
    foreach ($p in $procs) {
        Write-Host "[predev] found leftover dev-stack process pid $($p.ProcessId) (started $($p.CreationDate)) - stopping it"
        taskkill /F /T /PID $p.ProcessId 2>$null | Out-Null
    }
}

Free-Port 8000
Free-Port 3000
Kill-OrphanedDevProcesses

$redisExe = "C:\Program Files\Redis\redis-server.exe"
$redisListening = netstat -ano | Select-String ":6379 " | Select-String "LISTENING"

if ($redisListening) {
    Write-Host "[predev] redis already running on port 6379"
} elseif (Test-Path $redisExe) {
    Write-Host "[predev] starting redis..."
    Start-Process -FilePath $redisExe -ArgumentList "redis.windows-service.conf" -WorkingDirectory (Split-Path $redisExe) -WindowStyle Hidden
    Start-Sleep -Seconds 2
    if (netstat -ano | Select-String ":6379 " | Select-String "LISTENING") {
        Write-Host "[predev] redis started successfully"
    } else {
        Write-Warning "[predev] could not confirm redis startup - check C:\Program Files\Redis\Logs"
    }
} else {
    Write-Warning "[predev] redis-server.exe not found at $redisExe - start Redis yourself (e.g. docker compose up -d redis)"
}
