# Runs automatically before `npm run dev` (see the "predev" npm script).
# Three jobs:
#   1. Free ports 8000 (backend) and 3000 (frontend) if a previous run left
#      a zombie process behind - this project has repeatedly hit "address
#      already in use" from stale uvicorn/next processes surviving a
#      terminal being closed instead of the app being stopped cleanly.
#   2. Kill any orphaned ARQ worker process from a previous `npm run dev`
#      session. The worker (`arq backend.workers.arq_worker.WorkerSettings`)
#      binds no port at all, so Free-Port above never touches it - when a
#      previous dev session's `concurrently` parent process died/was closed
#      without cleanly killing its children (routine on Windows, where child
#      processes can outlive an already-gone parent), the worker survives
#      as an invisible zombie: still consuming CPU, still polling the same
#      shared Redis job queue as the new session's worker, and - since
#      Python doesn't hot-reload - still running whatever OLD code was
#      loaded when it started, silently out of sync with the current
#      source. Confirmed in practice: two such orphaned workers were found
#      alive here with dead parent PIDs, one of them holding a stale claim
#      on an in-progress job that blocked it from ever being picked up
#      again. Matched by command line (not by name/port) since the process
#      itself is just "python.exe".
#   3. Make sure Redis is up on 6379, starting it detached if needed. Redis
#      is deliberately NOT managed by `concurrently` in the "dev" script:
#      it holds job-queue/checkpoint state that must outlive individual dev
#      sessions, so we only ever start it here, never kill it here.

function Free-Port($port) {
    $lines = netstat -ano | Select-String ":$port " | Select-String "LISTENING"
    if (-not $lines) { return }
    $pids = $lines | ForEach-Object { ($_ -split '\s+')[-1] } | Sort-Object -Unique
    foreach ($p in $pids) {
        Write-Host "[predev] port $port is in use by pid $p - stopping it"
        taskkill /F /T /PID $p 2>$null | Out-Null
    }
}

function Kill-OrphanedArqWorkers {
    $workers = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match "arq\s+backend\.workers\.arq_worker" }
    foreach ($w in $workers) {
        Write-Host "[predev] found leftover ARQ worker pid $($w.ProcessId) (started $($w.CreationDate)) - stopping it"
        taskkill /F /T /PID $w.ProcessId 2>$null | Out-Null
    }
}

Free-Port 8000
Free-Port 3000
Kill-OrphanedArqWorkers

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
