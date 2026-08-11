"use client";

import { useQuery } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { useMemo } from "react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { CircuitBreakerIndicator } from "@/components/projects/CircuitBreakerIndicator";
import { CompanyTable } from "@/components/projects/CompanyTable";
import { ErrorLogPanel } from "@/components/projects/ErrorLogPanel";
import { JobToolbar } from "@/components/projects/JobToolbar";
import { LiveProgressBar } from "@/components/projects/LiveProgressBar";
import { ReuploadCsvDialog } from "@/components/projects/ReuploadCsvDialog";
import { StatsCards } from "@/components/projects/StatsCards";
import { useBackendToken } from "@/hooks/useBackendToken";
import { useSSEJob } from "@/hooks/useSSEJob";
import { api } from "@/lib/api";
import { useJobStore } from "@/store/jobStore";

export default function ProjectDetailPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const { data: token } = useBackendToken();

  const { data: project } = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(token as string, projectId),
    enabled: !!token,
  });

  const { data: jobs } = useQuery({
    queryKey: ["jobs", projectId],
    queryFn: () => api.listJobsForProject(token as string, projectId),
    enabled: !!token,
    refetchInterval: 15_000,
  });

  const latestJob = jobs?.[0] ?? null;

  // Deliberately project-wide, NOT scoped to latestJob.id: a company only
  // ever gets (re)processed by whichever job run first completes it, and is
  // skipped by every later run on this project (see backend/routers/jobs.py
  // create_and_start_job / retry_failed_companies). Parquet rows keep the
  // job_id of whichever run actually wrote them, so filtering stats by the
  // *latest* job's id would hide every company completed in an earlier run -
  // this dashboard should show the project's true cumulative totals.
  const { data: stats } = useQuery({
    queryKey: ["stats", projectId],
    queryFn: () => api.getStats(token as string, projectId),
    enabled: !!token,
    refetchInterval: latestJob?.status === "RUNNING" ? 5000 : false,
  });

  const { data: failedCompanies } = useQuery({
    queryKey: ["companies", "failed", projectId],
    queryFn: () => api.listCompanies(token as string, { projectId, status: "failed", limit: 200 }),
    enabled: !!token,
  });

  useSSEJob(latestJob?.id ?? null, token ?? null);
  const liveStatus = useJobStore((s) => (latestJob ? s.status[latestJob.id] : undefined));
  const sseProgress = useJobStore((s) => (latestJob ? s.progress[latestJob.id] : undefined));

  const effectiveJob = useMemo(() => {
    if (!latestJob) return null;
    return liveStatus ? { ...latestJob, status: liveStatus as typeof latestJob.status } : latestJob;
  }, [latestJob, liveStatus]);

  // `sseProgress` is empty until the first SSE PROGRESS event arrives after
  // a page load/refresh (or if the connection is still reconnecting), which
  // otherwise shows a misleading "0 / 0 companies processed" even though
  // the job list query (refetched every 15s) already has real counters.
  // Prefer the live SSE value once it exists, since it updates far more
  // often than the 15s poll; fall back to the polled job row until then.
  const liveProgress = useMemo(() => {
    if (sseProgress) return sseProgress;
    if (!latestJob) return undefined;
    const total = latestJob.total ?? 0;
    const done = latestJob.done ?? 0;
    const failed = latestJob.failed ?? 0;
    return {
      done,
      failed,
      total,
      percent: total ? Math.round(((done + failed) / total) * 100) : 0,
    };
  }, [sseProgress, latestJob]);

  const isActive = effectiveJob?.status === "RUNNING" || effectiveJob?.status === "QUEUED";
  const activeJobId =
    effectiveJob && ["RUNNING", "QUEUED", "PAUSED"].includes(effectiveJob.status) ? effectiveJob.id : null;

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{project?.name ?? "Loading..."}</h1>
          <p className="text-sm text-muted-foreground mt-1">{project?.description}</p>
        </div>
        <div className="flex flex-col items-end gap-2">
          <CircuitBreakerIndicator token={token ?? null} active={isActive} />
          <div className="flex items-center gap-2">
            <ReuploadCsvDialog projectId={projectId} token={token ?? null} activeJobId={activeJobId} />
            <JobToolbar
              job={effectiveJob}
              token={token ?? null}
              projectId={projectId}
              hasFailed={(failedCompanies?.total ?? 0) > 0}
              hasCompanies={(project?.total_companies ?? 0) > 0}
            />
          </div>
        </div>
      </div>

      {effectiveJob && (
        <div className="rounded-lg border p-4">
          <LiveProgressBar
            progress={liveProgress}
            status={effectiveJob.status}
            startedAt={effectiveJob.started_at}
          />
        </div>
      )}

      <StatsCards job={effectiveJob} totalProducts={stats?.total_products ?? 0} />

      <Tabs defaultValue="all">
        <TabsList>
          <TabsTrigger value="all">All Companies</TabsTrigger>
          <TabsTrigger value="failed">
            Failed {failedCompanies?.total ? `(${failedCompanies.total})` : ""}
          </TabsTrigger>
        </TabsList>
        <TabsContent value="all" className="mt-4">
          <CompanyTable projectId={projectId} token={token ?? null} />
        </TabsContent>
        <TabsContent value="failed" className="mt-4">
          <ErrorLogPanel failedCompanies={failedCompanies?.companies ?? []} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
