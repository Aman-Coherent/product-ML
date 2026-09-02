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
 * Same SSE wire protocol and Redis channel scheme as useSSEJob.ts (see
 * backend/core/email_job_engine.py's _publish - it reuses the exact same
 * SSEEvent schema/pub-sub channel as product-generation jobs, just keyed by
 * batch id instead of job id), and reuses the same zustand store (keyed
 * generically by id string) - only the per-event log-line text differs,
 * since email results carry different fields (primary_email/tier/website
 * source) than product-generation results (classification/products_count).
 */
export function useSSEEmailBatch(batchId: string | null, token: string | null) {
  const queryClient = useQueryClient();
  const { setStatus, setProgress, pushLog } = useJobStore();
  const esRef = useRef<EventSource | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const invalidateTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!batchId || !token) return;

    let cancelled = false;

    const scheduleCompaniesInvalidate = () => {
      if (invalidateTimer.current) return;
      invalidateTimer.current = setTimeout(() => {
        invalidateTimer.current = null;
        queryClient.invalidateQueries({ queryKey: ["email-companies"] });
      }, INVALIDATE_DEBOUNCE_MS);
    };

    const connect = () => {
      if (cancelled) return;

      const lastSeq = sessionStorage.getItem(`${LAST_EVENT_STORAGE_PREFIX}${batchId}`);
      const url = api.streamUrl(batchId, token) + (lastSeq ? `&last_event_id=${lastSeq}` : "");

      const es = new EventSource(url, { withCredentials: false });
      esRef.current = es;

      const handleMessage = (raw: MessageEvent) => {
        try {
          const payload: SSEPayload = JSON.parse(raw.data);
          sessionStorage.setItem(`${LAST_EVENT_STORAGE_PREFIX}${batchId}`, String(payload.seq));

          switch (payload.event) {
            case "progress": {
              const { done, failed, total, percent } = payload.data as {
                done: number;
                failed: number;
                total: number;
                percent: number;
              };
              setProgress(batchId, { done, failed, total, percent });
              break;
            }
            case "status_change": {
              const status = payload.data.status as string;
              setStatus(batchId, status);
              queryClient.invalidateQueries({ queryKey: ["email-batch", batchId] });
              break;
            }
            case "company_done": {
              const name = payload.data.company_name as string;
              const email = payload.data.primary_email as string | null;
              pushLog(batchId, email ? `Found: ${name} — ${email}` : `Done: ${name} — no email found`);
              scheduleCompaniesInvalidate();
              break;
            }
            case "company_failed": {
              const name = payload.data.company_name as string;
              const error = payload.data.error as string;
              pushLog(batchId, `Failed: ${name} — ${error}`);
              scheduleCompaniesInvalidate();
              break;
            }
            case "complete": {
              setStatus(batchId, "COMPLETED");
              toast.success("Email batch completed");
              queryClient.invalidateQueries({ queryKey: ["email-batch", batchId] });
              queryClient.invalidateQueries({ queryKey: ["email-batches"] });
              queryClient.invalidateQueries({ queryKey: ["email-companies"] });
              queryClient.invalidateQueries({ queryKey: ["email-stats"] });
              break;
            }
            case "error": {
              toast.error((payload.data.message as string) || "Batch encountered an error");
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
  }, [batchId, token, queryClient, setStatus, setProgress, pushLog]);
}
