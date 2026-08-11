"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { toast } from "sonner";

import { api } from "@/lib/api";
import { useJobStore } from "@/store/jobStore";

interface SSEPayload {
  event: string;
  job_id: string;
  data: Record<string, unknown>;
  seq: number;
}

const RECONNECT_DELAY_MS = 2000;
const LAST_EVENT_STORAGE_PREFIX = "sse_last_seq_";
const INVALIDATE_DEBOUNCE_MS = 1000;

/**
 * Subscribes to a job's live SSE stream. Reconnects automatically using
 * Last-Event-ID (persisted in sessionStorage) so a page refresh or brief
 * network drop never loses progress events — the backend replays
 * everything missed since the last seen sequence number.
 */
export function useSSEJob(jobId: string | null, token: string | null) {
  const queryClient = useQueryClient();
  const { setStatus, setProgress, pushLog } = useJobStore();
  const esRef = useRef<EventSource | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const invalidateTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!jobId || !token) return;

    let cancelled = false;

    // Coalesces bursts of per-company SSE events (up to `concurrency`/sec at
    // 200K-company scale) into at most one company-table refetch per second,
    // instead of triggering a network request per event.
    const scheduleCompaniesInvalidate = () => {
      if (invalidateTimer.current) return;
      invalidateTimer.current = setTimeout(() => {
        invalidateTimer.current = null;
        queryClient.invalidateQueries({ queryKey: ["companies"] });
      }, INVALIDATE_DEBOUNCE_MS);
    };

    const connect = () => {
      if (cancelled) return;

      const lastSeq = sessionStorage.getItem(`${LAST_EVENT_STORAGE_PREFIX}${jobId}`);
      const url = api.streamUrl(jobId, token) + (lastSeq ? `&last_event_id=${lastSeq}` : "");

      const es = new EventSource(url, { withCredentials: false });
      esRef.current = es;

      const handleMessage = (raw: MessageEvent) => {
        try {
          const payload: SSEPayload = JSON.parse(raw.data);
          sessionStorage.setItem(`${LAST_EVENT_STORAGE_PREFIX}${jobId}`, String(payload.seq));

          switch (payload.event) {
            case "progress": {
              const { done, failed, total, percent } = payload.data as {
                done: number;
                failed: number;
                total: number;
                percent: number;
              };
              setProgress(jobId, { done, failed, total, percent });
              break;
            }
            case "status_change": {
              const status = payload.data.status as string;
              setStatus(jobId, status);
              queryClient.invalidateQueries({ queryKey: ["job", jobId] });
              break;
            }
            case "company_done": {
              const name = payload.data.company_name as string;
              const source = payload.data.url_source as string;
              pushLog(jobId, `Done: ${name} (${source})`);
              scheduleCompaniesInvalidate();
              break;
            }
            case "company_failed": {
              const name = payload.data.company_name as string;
              const error = payload.data.error as string;
              pushLog(jobId, `Failed: ${name} — ${error}`);
              scheduleCompaniesInvalidate();
              break;
            }
            case "complete": {
              setStatus(jobId, "COMPLETED");
              toast.success("Job completed successfully");
              queryClient.invalidateQueries({ queryKey: ["job", jobId] });
              queryClient.invalidateQueries({ queryKey: ["jobs"] });
              queryClient.invalidateQueries({ queryKey: ["companies"] });
              queryClient.invalidateQueries({ queryKey: ["stats"] });
              break;
            }
            case "error": {
              toast.error((payload.data.message as string) || "Job encountered an error");
              break;
            }
          }
        } catch {
          // ignore malformed events
        }
      };

      es.addEventListener("progress", handleMessage);
      es.addEventListener("status_change", handleMessage);
      es.addEventListener("company_done", handleMessage);
      es.addEventListener("company_failed", handleMessage);
      es.addEventListener("complete", handleMessage);
      es.addEventListener("error", handleMessage);
      es.onmessage = handleMessage;

      es.onerror = () => {
        es.close();
        if (!cancelled) {
          reconnectTimer.current = setTimeout(connect, RECONNECT_DELAY_MS);
        }
      };
    };

    connect();

    return () => {
      cancelled = true;
      esRef.current?.close();
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      if (invalidateTimer.current) clearTimeout(invalidateTimer.current);
    };
  }, [jobId, token, queryClient, setStatus, setProgress, pushLog]);
}
