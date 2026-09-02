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
  useCancelEmailBatch,
  usePauseEmailBatch,
  useResumeEmailBatch,
  useRetryFailedEmailCompanies,
  useStartEmailBatch,
} from "@/hooks/useEmailBatchControl";
import { api } from "@/lib/api";
import type { EmailBatch } from "@/lib/api";

interface Props {
  batch: EmailBatch | null;
  batchId: string;
  token: string | null;
  hasFailed: boolean;
  hasCompanies: boolean;
}

const TERMINAL_STATUSES = new Set(["COMPLETED", "FAILED", "CANCELLED"]);

export function EmailBatchToolbar({ batch, batchId, token, hasFailed, hasCompanies }: Props) {
  const pause = usePauseEmailBatch(token, batchId);
  const resume = useResumeEmailBatch(token, batchId);
  const cancel = useCancelEmailBatch(token, batchId);
  const retry = useRetryFailedEmailCompanies(token, batchId);
  const start = useStartEmailBatch(token, batchId);

  const status = batch?.status;
  const canStart = !batch || TERMINAL_STATUSES.has(status ?? "");

  return (
    <div className="flex flex-wrap items-center gap-2">
      {canStart && (
        <Button
          size="sm"
          onClick={() => start.mutate(undefined)}
          disabled={start.isPending || !hasCompanies || !token}
          title={!hasCompanies ? "Upload a CSV of companies first" : undefined}
        >
          <Play className="mr-1.5 size-3.5" /> {batch ? "Start new run" : "Start"}
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
          <DropdownMenuItem render={<a href={token ? api.exportEmailBatchUrl(batchId, "csv", token) : "#"} />}>
            Export as CSV
          </DropdownMenuItem>
          <DropdownMenuItem render={<a href={token ? api.exportEmailBatchUrl(batchId, "json", token) : "#"} />}>
            Export as JSON
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
