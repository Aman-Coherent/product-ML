import { AlertTriangle, CheckCircle2, Package, Timer } from "lucide-react";

import type { Job } from "@/lib/api";

export function StatsCards({ job, totalProducts }: { job: Job | null; totalProducts: number }) {
  const cards = [
    {
      label: "Completed",
      value: job?.done?.toLocaleString() ?? "0",
      icon: CheckCircle2,
      color: "text-emerald-500",
    },
    {
      label: "Failed",
      value: job?.failed?.toLocaleString() ?? "0",
      icon: AlertTriangle,
      color: "text-red-500",
    },
    {
      label: "Products Generated",
      value: totalProducts.toLocaleString(),
      icon: Package,
      color: "text-blue-500",
    },
    {
      label: "Status",
      value: job?.status ?? "—",
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
