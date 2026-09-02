"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Mail } from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";
import { useState } from "react";

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
import { EmailBatchCard } from "@/components/emails/EmailBatchCard";
import { useBackendToken } from "@/hooks/useBackendToken";
import { api, ApiError } from "@/lib/api";

export default function EmailFinderPage() {
  const { data: token } = useBackendToken();
  const queryClient = useQueryClient();
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);

  const { data: batches, isLoading } = useQuery({
    queryKey: ["email-batches"],
    queryFn: () => api.listEmailBatches(token as string),
    enabled: !!token,
  });

  async function confirmDelete() {
    if (!pendingDeleteId || !token) return;
    try {
      await api.deleteEmailBatch(token, pendingDeleteId);
      queryClient.invalidateQueries({ queryKey: ["email-batches"] });
      toast.success("Batch deleted");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to delete batch");
    } finally {
      setPendingDeleteId(null);
    }
  }

  const totalCompanies = batches?.reduce((sum, b) => sum + b.total, 0) ?? 0;
  const activeBatches = batches?.filter((b) => b.status === "RUNNING").length ?? 0;

  return (
    <div className="mx-auto max-w-6xl space-y-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Email Finder</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Find companies&apos; real official contact emails from their websites — never invented.
          </p>
        </div>
        <Button
          render={
            <Link href="/dashboard/emails/new">
              <Mail className="mr-1.5 size-4" /> New Batch
            </Link>
          }
        />
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <StatCard label="Total Batches" value={batches?.length ?? 0} />
        <StatCard label="Companies Tracked" value={totalCompanies.toLocaleString()} />
        <StatCard label="Batches Running Now" value={activeBatches} />
      </div>

      {isLoading ? (
        <div className="flex justify-center py-24 text-muted-foreground">
          <Loader2 className="size-6 animate-spin" />
        </div>
      ) : batches && batches.length > 0 ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {batches.map((batch) => (
            <EmailBatchCard key={batch.id} batch={batch} onDelete={setPendingDeleteId} />
          ))}
        </div>
      ) : (
        <div className="flex flex-col items-center justify-center rounded-lg border border-dashed py-24 text-center">
          <Mail className="mb-3 size-8 text-muted-foreground" />
          <p className="font-medium">No email batches yet</p>
          <p className="mt-1 text-sm text-muted-foreground">
            Upload a company list to start finding their official contact emails.
          </p>
          <Button className="mt-4" render={<Link href="/dashboard/emails/new">Create a batch</Link>} />
        </div>
      )}

      <AlertDialog open={!!pendingDeleteId} onOpenChange={(open) => !open && setPendingDeleteId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this batch?</AlertDialogTitle>
            <AlertDialogDescription>
              This permanently removes the batch, its companies, and all found emails. This action cannot be
              undone.
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
