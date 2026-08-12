#!/usr/bin/env node
/**
 * Replaces `concurrently` for `npm run dev`.
 *
 * Why this exists: on Windows, `concurrently` (and npm scripts in general)
 * run each sub-process through `cmd.exe /c`. Because these children share
 * the parent's console (that's how their output shows up inline), a Ctrl+C
 * or "stop" on the terminal delivers a Ctrl+C break to the ENTIRE console
 * process group at once - including all three nested cmd.exe wrappers. Each
 * one independently traps that as "did a batch script just get interrupted?"
 * and blocks on its own interactive "Terminate batch job (Y/N)?" prompt
 * instead of exiting. If that prompt isn't answered "Y" for literally every
 * single one (routine when the stop button kills the top-level process
 * instead of typing into the terminal), the real processes underneath -
 * uvicorn, the ARQ worker, next dev - never actually die. They keep running
 * as invisible orphans: still bound to their ports/still polling the same
 * Redis queue as the NEXT `npm run dev` session's fresh copies, and (since
 * Python doesn't hot-reload) still serving whatever OLD code was loaded
 * when they started. Confirmed repeatedly in practice - see predev.ps1's
 * Kill-OrphanedArqWorkers/Free-Port, which only exist to mop this up
 * *after the fact* on the next startup.
 *
 * The fix: never let cmd.exe (or any shell) sit in the middle at all. Every
 * child below is spawned with `shell: false`, invoking either a real .exe
 * directly (.venv's python) or `node <script.js>` directly (Next's own bin
 * script, bypassing the npm.cmd shim) - there is no batch-file context left
 * anywhere in the tree, so there is nothing to show that prompt. A native
 * Ctrl+C now hits every process in this console's group directly and each
 * one runs its own real (already-graceful) SIGINT handler - uvicorn/arq's
 * own "shutting down" logic, not a shell pretending to babysit it.
 *
 * As a backstop (in case something ignores SIGINT, e.g. a wedged network
 * call), this script's own SIGINT/SIGTERM handler force-kills each child's
 * entire process tree via `taskkill /T /F` - `/T` matters here because
 * `.venv\Scripts\python.exe` on this machine is itself a thin shim that
 * re-execs the real interpreter as a CHILD process; killing only the shim's
 * own pid would leak the real one.
 */
import { spawn, execSync } from "node:child_process";
import path from "node:path";

const isWin = process.platform === "win32";
const root = process.cwd();
const frontendDir = path.join(root, "frontend");
const nextBin = path.join(frontendDir, "node_modules", "next", "dist", "bin", "next");

const specs = [
  {
    name: "backend",
    color: "34",
    cmd: path.join(root, ".venv", "Scripts", isWin ? "python.exe" : "python"),
    args: ["-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8000"],
    cwd: root,
  },
  {
    name: "worker",
    color: "35",
    cmd: path.join(root, ".venv", "Scripts", isWin ? "python.exe" : "python"),
    args: ["-m", "arq", "backend.workers.arq_worker.WorkerSettings"],
    cwd: root,
  },
  {
    // Invokes Next's own bin script directly via node instead of going
    // through the `npm --prefix frontend run dev` -> npm.cmd -> cmd.exe
    // chain, for the same "no shell in the tree" reason as above.
    name: "frontend",
    color: "32",
    cmd: process.execPath,
    args: [nextBin, "dev"],
    cwd: frontendDir,
  },
];

const children = [];
let shuttingDown = false;

function log(name, color, line) {
  if (line === "") return;
  process.stdout.write(`\x1b[${color}m[${name}]\x1b[0m ${line}\n`);
}

function pipe(spec, stream) {
  let buf = "";
  stream.on("data", (chunk) => {
    buf += chunk.toString();
    const lines = buf.split(/\r?\n/);
    buf = lines.pop();
    for (const line of lines) log(spec.name, spec.color, line);
  });
}

function killTree(child) {
  if (!child || child.exitCode !== null || child.pid == null) return;
  if (isWin) {
    try {
      execSync(`taskkill /PID ${child.pid} /T /F`, { stdio: "ignore" });
    } catch {
      // Already exited between the check above and here - fine.
    }
  } else {
    try {
      process.kill(child.pid, "SIGKILL");
    } catch {
      // Already gone.
    }
  }
}

function shutdown(exitCode) {
  if (shuttingDown) return;
  shuttingDown = true;
  process.stdout.write("\n--> Stopping backend, worker, frontend...\n");
  for (const child of children) killTree(child);
  process.exit(exitCode);
}

for (const spec of specs) {
  const child = spawn(spec.cmd, spec.args, {
    cwd: spec.cwd,
    shell: false,
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
  });
  children.push(child);
  pipe(spec, child.stdout);
  pipe(spec, child.stderr);

  child.on("error", (err) => {
    log(spec.name, spec.color, `failed to start: ${err.message}`);
    shutdown(1);
  });

  child.on("exit", (code) => {
    if (shuttingDown) return;
    log(spec.name, spec.color, `exited with code ${code} - stopping the rest`);
    shutdown(code ?? 1);
  });
}

for (const sig of ["SIGINT", "SIGTERM", "SIGBREAK"]) {
  process.on(sig, () => shutdown(0));
}
