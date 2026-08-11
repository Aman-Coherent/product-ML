"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { api, ApiError } from "@/lib/api";

function handleError(err: unknown) {
  const message = err instanceof ApiError ? err.message : (err as Error)?.message;
  toast.error(message || "Something went wrong");
}

export function useStartJob(token: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { project_id: string; mode?: string; concurrency?: number }) => {
      if (!token) throw new Error("Please wait a moment for your session to load.");
      return api.createJob(token, body);
    },
    onError: handleError,
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      queryClient.invalidateQueries({ queryKey: ["jobs", variables.project_id] });
      toast.success("Job started");
    },
  });
}

export function usePauseJob(token: string | null, jobId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => {
      if (!token) throw new Error("Please wait a moment for your session to load.");
      return api.pauseJob(token, jobId);
    },
    onError: handleError,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["job", jobId] });
      toast.info("Pausing job — current companies will finish first");
    },
  });
}

export function useResumeJob(token: string | null, jobId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => {
      if (!token) throw new Error("Please wait a moment for your session to load.");
      return api.resumeJob(token, jobId);
    },
    onError: handleError,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["job", jobId] });
      toast.success("Job resumed");
    },
  });
}

export function useCancelJob(token: string | null, jobId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => {
      if (!token) throw new Error("Please wait a moment for your session to load.");
      return api.cancelJob(token, jobId);
    },
    onError: handleError,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["job", jobId] });
      toast.info("Stopping job — in-flight requests are being cancelled now");
    },
  });
}

export function useRetryFailed(token: string | null, jobId: string, projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => {
      if (!token) throw new Error("Please wait a moment for your session to load.");
      return api.retryFailed(token, jobId);
    },
    onError: handleError,
    onSuccess: (newJob) => {
      // retry-failed creates a brand new job (see api.retryFailed) - refresh
      // the project's job list so it picks up as the new "latest" job.
      queryClient.invalidateQueries({ queryKey: ["job", jobId] });
      queryClient.invalidateQueries({ queryKey: ["jobs", projectId] });
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      queryClient.invalidateQueries({ queryKey: ["companies"] });
      // At creation time `done` is pre-seeded with already-successful
      // companies and everything else in scope is exactly the failed set,
      // so total - done is the retried count.
      toast.success(`Retrying ${Math.max(newJob.total - newJob.done, 0)} failed companies`);
    },
  });
}
