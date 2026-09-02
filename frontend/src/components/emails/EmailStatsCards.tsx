import { AlertTriangle, CheckCircle2, Compass, HelpCircle, Mail, SearchX, Timer } from "lucide-react";

import type { EmailBatch, EmailBatchStats, EmailCategory } from "@/lib/api";
import { cn } from "@/lib/utils";

// Category tiles double as filter buttons (see EmailBatchDetailPage) - at
// real scale (tens of thousands of companies per batch) scrolling the
// table to eyeball a breakdown isn't practical, so clicking a number here
// jumps straight to just those rows via the server-side filter.
export function EmailStatsCards({
  batch,
  stats,
  activeCategory,
  onCategoryClick,
}: {
  batch: EmailBatch | null;
  stats: EmailBatchStats | undefined;
  activeCategory?: EmailCategory | null;
  onCategoryClick?: (category: EmailCategory | null) => void;
}) {
  const byCategory = stats?.by_category;

  const topCards = [
    { label: "Completed", value: batch?.done?.toLocaleString() ?? "0", icon: CheckCircle2, color: "text-emerald-500" },
    { label: "Failed", value: batch?.failed?.toLocaleString() ?? "0", icon: AlertTriangle, color: "text-red-500" },
    { label: "Status", value: batch?.status ?? "—", icon: Timer, color: "text-muted-foreground" },
  ];

  const categoryCards: { key: EmailCategory; label: string; icon: typeof Mail; color: string }[] = [
    { key: "found_given", label: "Found on given website", icon: Mail, color: "text-emerald-500" },
    { key: "found_discovered", label: "Found on discovered website", icon: Compass, color: "text-blue-500" },
    { key: "guessed", label: "Guessed (not found)", icon: HelpCircle, color: "text-amber-600" },
    { key: "not_found", label: "No website found", icon: SearchX, color: "text-red-500" },
  ];

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        {topCards.map((c) => (
          <div key={c.label} className="rounded-lg border bg-card p-4">
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <c.icon className={`size-3.5 ${c.color}`} />
              {c.label}
            </div>
            <p className="mt-1.5 text-xl font-semibold tabular-nums">{c.value}</p>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {categoryCards.map((c) => {
          const isActive = activeCategory === c.key;
          const value = byCategory?.[c.key] ?? 0;
          return (
            <button
              key={c.key}
              type="button"
              onClick={() => onCategoryClick?.(isActive ? null : c.key)}
              className={cn(
                "rounded-lg border bg-card p-4 text-left transition-colors",
                onCategoryClick && "cursor-pointer hover:bg-muted/40",
                isActive && "border-primary bg-primary/5"
              )}
            >
              <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <c.icon className={`size-3.5 ${c.color}`} />
                {c.label}
              </div>
              <p className="mt-1.5 text-xl font-semibold tabular-nums">{value.toLocaleString()}</p>
            </button>
          );
        })}
      </div>
    </div>
  );
}
