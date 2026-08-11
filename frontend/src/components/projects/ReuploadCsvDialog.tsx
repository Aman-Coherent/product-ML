"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { CsvUploadZone } from "@/components/projects/CsvUploadZone";
import { useCancelJob } from "@/hooks/useJobControl";
import { api, ApiError, type UploadCsvResult } from "@/lib/api";

interface Props {
  projectId: string;
  token: string | null;
  activeJobId: string | null; // a RUNNING/QUEUED/PAUSED job, if any
}

/**
 * Re-uploading a CSV always fully replaces every company row for this
 * project (by design, to avoid duplicates on re-upload) — there is no
 * partial-update path. That's fine for a brand new project, but for a
 * project with an in-progress or completed job, it means:
 *   - the active job (if any) MUST be cancelled first, since it holds
 *     row-level references (checkpoints, in-flight tasks) that would
 *     otherwise point at company rows that no longer exist, and
 *   - already-generated results for this project effectively become
 *     orphaned (still exported under the old job_id, but the company
 *     list backing the live table view is gone).
 * This dialog makes that trade-off explicit instead of silently doing it.
 */
export function ReuploadCsvDialog({ projectId, token, activeJobId }: Props) {
  const [open, setOpen] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadResult, setUploadResult] = useState<UploadCsvResult | null>(null);
  const queryClient = useQueryClient();
  const cancelJob = useCancelJob(token, activeJobId ?? "");

  function reset() {
    setConfirmed(false);
    setUploadResult(null);
  }

  async function handleFileSelected(file: File) {
    if (!token) {
      toast.error("Your session isn't ready yet. Please wait a moment and try again.");
      return;
    }
    setIsUploading(true);
    setUploadResult(null);
    try {
      if (activeJobId) {
        await cancelJob.mutateAsync();
      }
      const result = await api.uploadCsv(token, projectId, file);
      setUploadResult(result);
      queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      queryClient.invalidateQueries({ queryKey: ["jobs", projectId] });
      queryClient.invalidateQueries({ queryKey: ["companies"] });
      toast.success(`${result.total_rows.toLocaleString()} companies loaded — ready to start a new job`);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to upload CSV");
    } finally {
      setIsUploading(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) reset();
      }}
    >
      <DialogTrigger
        render={
          <Button size="sm" variant="outline">
            <RefreshCw className="mr-1.5 size-3.5" /> Re-upload CSV
          </Button>
        }
      />
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Re-upload company list</DialogTitle>
          <DialogDescription>
            This replaces every company row in this project with the new file's contents.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="flex gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-600 dark:text-amber-400">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" />
            <div className="space-y-1">
              <p className="font-medium">This cannot be undone.</p>
              <p>
                All current companies and their processing status will be deleted and replaced.
                {activeJobId && " The current job will be cancelled first."} Already-generated
                products for this project will no longer appear in the table (existing exports are
                unaffected). Every company — including ones already processed before — will need to
                be reprocessed from scratch by starting a new job afterward.
              </p>
            </div>
          </div>

          {!confirmed ? (
            <Button variant="destructive" className="w-full" onClick={() => setConfirmed(true)}>
              I understand, let me choose a file
            </Button>
          ) : (
            <CsvUploadZone
              onFileSelected={handleFileSelected}
              uploadResult={uploadResult}
              isUploading={isUploading || cancelJob.isPending}
            />
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            {uploadResult ? "Done" : "Close"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
