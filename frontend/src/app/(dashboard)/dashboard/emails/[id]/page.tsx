"use client";

import { useQuery } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { useMemo } from "react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { EmailBatchToolbar } from "@/components/emails/EmailBatchToolbar";
import { EmailCompanyTable } from "@/components/emails/EmailCompanyTable";
import { EmailErrorLogPanel } from "@/components/emails/EmailErrorLogPanel";
import { EmailStatsCards } from "@/components/emails/EmailStatsCards";
import { LiveProgressBar } from "@/components/projects/LiveProgressBar";
import { useBackendToken } from "@/hooks/useBackendToken";
import { useSSEEmailBatch } from "@/hooks/useSSEEmailBatch";
import { api } from "@/lib/api";
import { useJobStore } from "@/store/jobStore";

export default function EmailBatchDetailPage() {
  const params = useParams<{ id: string }>();
  const batchId = params.id;
  const { data: token } = useBackendToken();

  const { data: batch } = useQuery({
    queryKey: ["email-batch", batchId],
    queryFn: () => api.getEmailBatch(token as string, batchId),
    enabled: !!token,
    refetchInterval: 15_000,
  });

  // Deliberately batch-wide, not job-run scoped - a batch here has no
  // separate Job-table concept the way Projects do (see EmailBatch's own
  // done/failed/total counters), so stats are always just "this batch's
  // current state".
  const { data: stats } = useQuery({
    queryKey: ["email-stats", batchId],
    queryFn: () => api.getEmailBatchStats(token as string, batchId),
    enabled: !!token,
    refetchInterval: batch?.status === "RUNNING" ? 5000 : false,
  });

  const { data: failedCompanies } = useQuery({
    queryKey: ["email-companies", "failed", batchId],
    queryFn: () => api.listEmailCompanies(token as string, { batchId, status: "failed", limit: 200 }),
    enabled: !!token,
  });

  useSSEEmailBatch(batch ? batchId : null, token ?? null);
  const liveStatus = useJobStore((s) => s.status[batchId]);
  const sseProgress = useJobStore((s) => s.progress[batchId]);

  const effectiveBatch = useMemo(() => {
    if (!batch) return null;
    return liveStatus ? { ...batch, status: liveStatus as typeof batch.status } : batch;
  }, [batch, liveStatus]);

  const liveProgress = useMemo(() => {
    if (sseProgress) return sseProgress;
    if (!batch) return undefined;
    const total = batch.total ?? 0;
    const done = batch.done ?? 0;
    const failed = batch.failed ?? 0;
    return {
      done,
      failed,
      total,
      percent: total ? Math.round(((done + failed) / total) * 100) : 0,
    };
  }, [sseProgress, batch]);

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{batch?.name ?? "Loading..."}</h1>
          <p className="text-sm text-muted-foreground mt-1">Email finder batch</p>
        </div>
        <EmailBatchToolbar
          batch={effectiveBatch}
          batchId={batchId}
          token={token ?? null}
          hasFailed={(failedCompanies?.total ?? 0) > 0}
          hasCompanies={(batch?.total ?? 0) > 0}
        />
      </div>

      {effectiveBatch && (
        <div className="rounded-lg border p-4">
          <LiveProgressBar progress={liveProgress} status={effectiveBatch.status} startedAt={effectiveBatch.started_at} />
        </div>
      )}

      <EmailStatsCards batch={effectiveBatch} stats={stats} />

      <Tabs defaultValue="all">
        <TabsList>
          <TabsTrigger value="all">All Companies</TabsTrigger>
          <TabsTrigger value="failed">
            Failed {failedCompanies?.total ? `(${failedCompanies.total})` : ""}
          </TabsTrigger>
        </TabsList>
        <TabsContent value="all" className="mt-4">
          <EmailCompanyTable batchId={batchId} token={token ?? null} />
        </TabsContent>
        <TabsContent value="failed" className="mt-4">
          <EmailErrorLogPanel failedCompanies={failedCompanies?.companies ?? []} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
