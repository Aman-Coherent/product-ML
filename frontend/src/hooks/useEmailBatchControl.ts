"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { api, ApiError } from "@/lib/api";

function handleError(err: unknown) {
  const message = err instanceof ApiError ? err.message : (err as Error)?.message;
  toast.error(message || "Something went wrong");
}

export function useStartEmailBatch(token: string | null, batchId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (concurrency?: number) => {
      if (!token) throw new Error("Please wait a moment for your session to load.");
      return api.startEmailBatch(token, batchId, concurrency);
    },
    onError: handleError,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["email-batches"] });
      queryClient.invalidateQueries({ queryKey: ["email-batch", batchId] });
      toast.success("Batch started");
    },
  });
}

export function usePauseEmailBatch(token: string | null, batchId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => {
      if (!token) throw new Error("Please wait a moment for your session to load.");
      return api.pauseEmailBatch(token, batchId);
    },
    onError: handleError,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["email-batch", batchId] });
      toast.info("Pausing — in-flight companies will finish first");
    },
  });
}

export function useResumeEmailBatch(token: string | null, batchId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => {
      if (!token) throw new Error("Please wait a moment for your session to load.");
      return api.resumeEmailBatch(token, batchId);
    },
    onError: handleError,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["email-batch", batchId] });
      toast.success("Batch resumed");
    },
  });
}

export function useCancelEmailBatch(token: string | null, batchId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => {
      if (!token) throw new Error("Please wait a moment for your session to load.");
      return api.cancelEmailBatch(token, batchId);
    },
    onError: handleError,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["email-batch", batchId] });
      toast.info("Stopping — in-flight requests are being cancelled now");
    },
  });
}

export function useRetryFailedEmailCompanies(token: string | null, batchId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => {
      if (!token) throw new Error("Please wait a moment for your session to load.");
      return api.retryFailedEmailCompanies(token, batchId);
    },
    onError: handleError,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["email-batch", batchId] });
      queryClient.invalidateQueries({ queryKey: ["email-companies"] });
      toast.success("Retrying failed companies");
    },
  });
}
