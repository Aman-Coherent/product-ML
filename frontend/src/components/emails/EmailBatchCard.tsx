"use client";

import { formatDistanceToNow } from "date-fns";
import { ArrowRight, Building2, Trash2 } from "lucide-react";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import type { EmailBatch } from "@/lib/api";

const STATUS_STYLES: Record<string, string> = {
  RUNNING: "bg-blue-500/15 text-blue-500 border-blue-500/30",
  QUEUED: "bg-amber-500/15 text-amber-500 border-amber-500/30",
  PAUSED: "bg-orange-500/15 text-orange-500 border-orange-500/30",
  COMPLETED: "bg-emerald-500/15 text-emerald-500 border-emerald-500/30",
  FAILED: "bg-red-500/15 text-red-500 border-red-500/30",
  CANCELLED: "bg-muted text-muted-foreground",
};

export function EmailBatchCard({
  batch,
  onDelete,
}: {
  batch: EmailBatch;
  onDelete: (id: string) => void;
}) {
  return (
    <Card className="group relative overflow-hidden transition-shadow hover:shadow-md">
      <CardHeader className="flex flex-row items-start justify-between gap-2 space-y-0">
        <div className="min-w-0">
          <CardTitle className="truncate text-base">{batch.name}</CardTitle>
          <p className="mt-1 text-xs text-muted-foreground">Email finder batch</p>
        </div>
        <Badge variant="outline" className={STATUS_STYLES[batch.status] ?? ""}>
          {batch.status}
        </Badge>
      </CardHeader>
      <CardContent>
        <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
          <Building2 className="h-3.5 w-3.5" />
          {batch.total.toLocaleString()} companies
          {batch.total > 0 && (
            <span className="text-xs">
              ({batch.done.toLocaleString()} done{batch.failed > 0 ? `, ${batch.failed.toLocaleString()} failed` : ""})
            </span>
          )}
        </div>
      </CardContent>
      <CardFooter className="flex items-center justify-between">
        <span className="text-xs text-muted-foreground">
          Updated {formatDistanceToNow(new Date(batch.created_at), { addSuffix: true })}
        </span>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            className="size-8 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 hover:text-destructive"
            onClick={() => onDelete(batch.id)}
            aria-label="Delete batch"
          >
            <Trash2 className="size-4" />
          </Button>
          <Button
            size="sm"
            variant="secondary"
            render={
              <Link href={`/dashboard/emails/${batch.id}`}>
                Open <ArrowRight className="ml-1 size-3.5" />
              </Link>
            }
          />
        </div>
      </CardFooter>
    </Card>
  );
}
