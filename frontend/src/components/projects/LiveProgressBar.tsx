"use client";

import { useEffect, useState } from "react";

import { Progress } from "@/components/ui/progress";
import type { JobProgress } from "@/store/jobStore";

interface Props {
  progress: JobProgress | undefined;
  status: string;
  startedAt: string | null;
}

function formatDuration(ms: number): string {
  const totalSeconds = Math.max(0, Math.round(ms / 1000));
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = totalSeconds % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

export function LiveProgressBar({ progress, status, startedAt }: Props) {
  const [now, setNow] = useState(() => Date.now());
  // Pure derivation from the `startedAt` prop — no need for extra state/effect.
  const startedAtMs = startedAt ? new Date(startedAt).getTime() : null;

  useEffect(() => {
    if (status !== "RUNNING") return;
    const interval = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(interval);
  }, [status]);

  const done = progress?.done ?? 0;
  const failed = progress?.failed ?? 0;
  const total = progress?.total ?? 0;
  const processed = done + failed;
  const percent = progress?.percent ?? (total ? Math.round((processed / total) * 100) : 0);

  let eta: string | null = null;
  if (status === "RUNNING" && startedAtMs && processed > 0 && total > processed) {
    const elapsed = now - startedAtMs;
    const rate = processed / elapsed; // companies per ms
    const remaining = total - processed;
    eta = formatDuration(remaining / rate);
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-sm">
        <span className="font-medium">
          {processed.toLocaleString()} / {total.toLocaleString()} companies processed
        </span>
        <span className="text-muted-foreground">
          {percent}% {eta && status === "RUNNING" ? `· ETA ${eta}` : ""}
        </span>
      </div>
      <Progress value={percent} className="h-2.5" />
      <div className="flex gap-4 text-xs text-muted-foreground">
        <span className="text-emerald-500">{done.toLocaleString()} done</span>
        {failed > 0 && <span className="text-red-500">{failed.toLocaleString()} failed</span>}
      </div>
    </div>
  );
}
