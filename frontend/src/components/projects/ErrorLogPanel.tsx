"use client";

import { AlertCircle } from "lucide-react";

import { ScrollArea } from "@/components/ui/scroll-area";
import type { Company } from "@/lib/api";

export function ErrorLogPanel({ failedCompanies }: { failedCompanies: Company[] }) {
  if (failedCompanies.length === 0) {
    return (
      <div className="flex items-center justify-center rounded-lg border border-dashed py-10 text-sm text-muted-foreground">
        No failed companies. Nice.
      </div>
    );
  }

  return (
    <ScrollArea className="h-72 rounded-lg border">
      <div className="divide-y">
        {failedCompanies.map((c) => (
          <div key={c.id} className="flex items-start gap-2 p-3">
            <AlertCircle className="mt-0.5 size-4 shrink-0 text-red-500" />
            <div className="min-w-0">
              <p className="text-sm font-medium truncate">{c.company_name}</p>
              <p className="text-xs text-muted-foreground truncate">
                {c.error_message || c.url_error || "Unknown error"}
              </p>
            </div>
          </div>
        ))}
      </div>
    </ScrollArea>
  );
}
