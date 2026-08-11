"use client";

import { Download, Pause, Play, RotateCcw, Square } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  useCancelJob,
  usePauseJob,
  useResumeJob,
  useRetryFailed,
  useStartJob,
} from "@/hooks/useJobControl";
import { api } from "@/lib/api";
import type { Job } from "@/lib/api";

interface Props {
  job: Job | null;
  token: string | null;
  projectId: string;
  hasFailed: boolean;
  hasCompanies: boolean;
}

const TERMINAL_STATUSES = new Set(["COMPLETED", "FAILED", "CANCELLED"]);

export function JobToolbar({ job, token, projectId, hasFailed, hasCompanies }: Props) {
  const jobId = job?.id ?? "";
  const pause = usePauseJob(token, jobId);
  const resume = useResumeJob(token, jobId);
  const cancel = useCancelJob(token, jobId);
  const retry = useRetryFailed(token, jobId, projectId);
  const start = useStartJob(token);

  const status = job?.status;
  const canStart = !job || TERMINAL_STATUSES.has(status ?? "");

  return (
    <div className="flex flex-wrap items-center gap-2">
      {canStart && (
        <Button
          size="sm"
          onClick={() => start.mutate({ project_id: projectId })}
          disabled={start.isPending || !hasCompanies || !token}
          title={!hasCompanies ? "Upload a CSV of companies first" : undefined}
        >
          <Play className="mr-1.5 size-3.5" /> {job ? "Start new run" : "Start job"}
        </Button>
      )}
      {status === "RUNNING" && (
        <Button variant="outline" size="sm" onClick={() => pause.mutate()} disabled={pause.isPending}>
          <Pause className="mr-1.5 size-3.5" /> Pause
        </Button>
      )}
      {status === "PAUSED" && (
        <Button variant="outline" size="sm" onClick={() => resume.mutate()} disabled={resume.isPending}>
          <Play className="mr-1.5 size-3.5" /> Resume
        </Button>
      )}
      {(status === "RUNNING" || status === "PAUSED" || status === "QUEUED") && (
        <Button variant="outline" size="sm" onClick={() => cancel.mutate()} disabled={cancel.isPending}>
          <Square className="mr-1.5 size-3.5" /> Stop
        </Button>
      )}
      {hasFailed && (status === "COMPLETED" || status === "FAILED") && (
        <Button variant="outline" size="sm" onClick={() => retry.mutate()} disabled={retry.isPending}>
          <RotateCcw className="mr-1.5 size-3.5" /> Retry failed
        </Button>
      )}

      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <Button variant="secondary" size="sm">
              <Download className="mr-1.5 size-3.5" /> Export
            </Button>
          }
        />
        <DropdownMenuContent align="end">
          {/* Deliberately no job_id: export the whole project's cumulative
              results, not just whichever job happens to be latest - a
              company completed by an earlier run is otherwise silently
              missing (see backend/storage/duckdb_queries.py job_id filter). */}
          <DropdownMenuItem render={<a href={token ? api.exportUrl(projectId, "csv", token) : "#"} />}>
            Export as CSV
          </DropdownMenuItem>
          <DropdownMenuItem render={<a href={token ? api.exportUrl(projectId, "json", token) : "#"} />}>
            Export as JSON
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
