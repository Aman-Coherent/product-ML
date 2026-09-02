import { AlertTriangle, CheckCircle2, Mail, Timer } from "lucide-react";

import type { EmailBatch, EmailBatchStats } from "@/lib/api";

export function EmailStatsCards({ batch, stats }: { batch: EmailBatch | null; stats: EmailBatchStats | undefined }) {
  const cards = [
    {
      label: "Completed",
      value: batch?.done?.toLocaleString() ?? "0",
      icon: CheckCircle2,
      color: "text-emerald-500",
    },
    {
      label: "Failed",
      value: batch?.failed?.toLocaleString() ?? "0",
      icon: AlertTriangle,
      color: "text-red-500",
    },
    {
      label: "Emails Found",
      value: (stats?.with_email ?? 0).toLocaleString(),
      icon: Mail,
      color: "text-blue-500",
    },
    {
      label: "Status",
      value: batch?.status ?? "—",
      icon: Timer,
      color: "text-muted-foreground",
    },
  ];

  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
      {cards.map((c) => (
        <div key={c.label} className="rounded-lg border bg-card p-4">
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <c.icon className={`size-3.5 ${c.color}`} />
            {c.label}
          </div>
          <p className="mt-1.5 text-xl font-semibold tabular-nums">{c.value}</p>
        </div>
      ))}
    </div>
  );
}
