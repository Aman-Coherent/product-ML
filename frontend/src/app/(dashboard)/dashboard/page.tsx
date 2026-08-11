"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FolderPlus, Loader2 } from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { ProjectCard } from "@/components/dashboard/ProjectCard";
import { useBackendToken } from "@/hooks/useBackendToken";
import { api, ApiError } from "@/lib/api";
import { useState } from "react";

export default function DashboardPage() {
  const { data: token } = useBackendToken();
  const queryClient = useQueryClient();
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);

  const { data: projects, isLoading } = useQuery({
    queryKey: ["projects"],
    queryFn: () => api.listProjects(token as string),
    enabled: !!token,
  });

  async function confirmDelete() {
    if (!pendingDeleteId || !token) return;
    try {
      await api.deleteProject(token, pendingDeleteId);
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      toast.success("Project deleted");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to delete project");
    } finally {
      setPendingDeleteId(null);
    }
  }

  const totalCompanies = projects?.reduce((sum, p) => sum + p.total_companies, 0) ?? 0;
  const activeJobs = projects?.filter((p) => p.latest_job_status === "RUNNING").length ?? 0;

  return (
    <div className="mx-auto max-w-6xl space-y-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Manage your classification and product generation projects.
          </p>
        </div>
        <Button
          render={
            <Link href="/dashboard/projects/new">
              <FolderPlus className="mr-1.5 size-4" /> New Project
            </Link>
          }
        />
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <StatCard label="Total Projects" value={projects?.length ?? 0} />
        <StatCard label="Companies Tracked" value={totalCompanies.toLocaleString()} />
        <StatCard label="Jobs Running Now" value={activeJobs} />
      </div>

      {isLoading ? (
        <div className="flex justify-center py-24 text-muted-foreground">
          <Loader2 className="size-6 animate-spin" />
        </div>
      ) : projects && projects.length > 0 ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {projects.map((project) => (
            <ProjectCard key={project.id} project={project} onDelete={setPendingDeleteId} />
          ))}
        </div>
      ) : (
        <div className="flex flex-col items-center justify-center rounded-lg border border-dashed py-24 text-center">
          <FolderPlus className="mb-3 size-8 text-muted-foreground" />
          <p className="font-medium">No projects yet</p>
          <p className="mt-1 text-sm text-muted-foreground">
            Create your first project to start classifying companies and generating products.
          </p>
          <Button className="mt-4" render={<Link href="/dashboard/projects/new">Create a project</Link>} />
        </div>
      )}

      <AlertDialog open={!!pendingDeleteId} onOpenChange={(open) => !open && setPendingDeleteId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this project?</AlertDialogTitle>
            <AlertDialogDescription>
              This permanently removes the project, its companies, and all generated products. This
              action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={confirmDelete} className="bg-destructive text-white hover:bg-destructive/90">
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border bg-card p-4">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
    </div>
  );
}
