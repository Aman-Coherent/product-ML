"use client";

import { formatDistanceToNow } from "date-fns";
import { Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { api, ApiError, type ApiKey, type KeyUsage } from "@/lib/api";

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
}

function UsageCell({ usage }: { usage: KeyUsage | undefined }) {
  if (!usage || usage.models.length === 0) {
    return <span className="text-xs text-muted-foreground">Not used yet</span>;
  }

  return (
    <Tooltip>
      <TooltipTrigger className="cursor-default text-left">
        <p className="text-sm font-medium tabular-nums">
          {formatNumber(usage.requests_today)} req &middot; {formatNumber(usage.tokens_today)} tok
        </p>
        <p className="text-xs text-muted-foreground">today, across {usage.models.length} models</p>
      </TooltipTrigger>
      <TooltipContent side="left" className="max-w-none">
        <div className="space-y-1">
          {usage.models.map((m) => (
            <div key={m.tag} className="flex items-center justify-between gap-4 text-xs">
              <span className="font-mono">{m.tag}</span>
              <span className="tabular-nums">
                {m.requests_today}
                {m.limit_requests_per_day ? `/${m.limit_requests_per_day}` : ""} req &middot;{" "}
                {formatNumber(m.tokens_today)}
                {m.limit_tokens_per_day ? `/${formatNumber(m.limit_tokens_per_day)}` : ""} tok
              </span>
            </div>
          ))}
        </div>
      </TooltipContent>
    </Tooltip>
  );
}

function RemainingCell({ usage }: { usage: KeyUsage | undefined }) {
  if (!usage) return <span className="text-xs text-muted-foreground">&mdash;</span>;

  const tracked = usage.models.filter((m) => m.limit_requests_per_day != null || m.limit_tokens_per_day != null);
  if (tracked.length === 0) {
    return <span className="text-xs text-muted-foreground">Not published</span>;
  }

  // Whichever model is closest to being exhausted is the most useful single
  // number to surface at a glance - the full per-model breakdown is one
  // hover away via UsageCell's tooltip.
  const tightest = tracked.reduce((worst, m) => {
    const worstFrac = worst.limit_requests_per_day ? (worst.remaining_requests_today ?? 0) / worst.limit_requests_per_day : 1;
    const currentFrac = m.limit_requests_per_day ? (m.remaining_requests_today ?? 0) / m.limit_requests_per_day : 1;
    return currentFrac < worstFrac ? m : worst;
  });

  const pct = tightest.limit_requests_per_day
    ? Math.round(((tightest.remaining_requests_today ?? 0) / tightest.limit_requests_per_day) * 100)
    : null;

  return (
    <div className="text-xs">
      <p className={pct !== null && pct < 20 ? "font-medium text-destructive" : "font-medium"}>
        {tightest.remaining_requests_today ?? "—"}/{tightest.limit_requests_per_day ?? "—"} req left
      </p>
      <p className="text-muted-foreground font-mono">{tightest.tag}</p>
    </div>
  );
}

export function ApiKeyTable({
  keys,
  usage,
  token,
  onChanged,
}: {
  keys: ApiKey[];
  usage?: KeyUsage[];
  token: string | null;
  onChanged: () => void;
}) {
  async function handleToggle(id: string) {
    if (!token) return;
    try {
      await api.toggleApiKey(token, id);
      onChanged();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to update key");
    }
  }

  async function handleDelete(id: string) {
    if (!token) return;
    try {
      await api.deleteApiKey(token, id);
      toast.success("Key removed");
      onChanged();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to delete key");
    }
  }

  const usageByRef = new Map((usage ?? []).map((u) => [u.key_ref, u]));
  const systemUsage = (usage ?? []).filter((u) => u.is_system);
  // Mirrors backend/core/llm_router.py's priority rule: an active user key
  // for groq/mistral fully replaces the matching system pool for that
  // provider, it isn't pooled alongside it - so the table should say so
  // instead of implying the (now-idle) system rows are still in the mix.
  const overriddenProviders = new Set(
    keys.filter((k) => k.is_active && (k.provider === "groq" || k.provider === "mistral")).map((k) => k.provider)
  );

  if (keys.length === 0 && systemUsage.length === 0) {
    return (
      <div className="flex items-center justify-center rounded-lg border border-dashed py-10 text-sm text-muted-foreground">
        No API keys added yet. The system-provided Groq and Mistral keys will be used by default.
      </div>
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Provider</TableHead>
          <TableHead>Label</TableHead>
          <TableHead>Key</TableHead>
          <TableHead>Used today</TableHead>
          <TableHead>Remaining</TableHead>
          <TableHead>Added</TableHead>
          <TableHead>Active</TableHead>
          <TableHead className="text-right">Actions</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {systemUsage.map((u) => {
          const overridden = overriddenProviders.has(u.provider);
          return (
            <TableRow key={u.key_ref} className={overridden ? "opacity-50" : "bg-muted/30"}>
              <TableCell>
                <Badge variant="outline" className="capitalize font-normal">
                  {u.provider}
                </Badge>
              </TableCell>
              <TableCell className="font-medium">{u.label}</TableCell>
              <TableCell className="font-mono text-xs text-muted-foreground">{u.masked_key}</TableCell>
              <TableCell>
                <UsageCell usage={u} />
              </TableCell>
              <TableCell>
                <RemainingCell usage={u} />
              </TableCell>
              <TableCell className="text-xs text-muted-foreground">System pool</TableCell>
              <TableCell>
                {overridden ? (
                  <Tooltip>
                    <TooltipTrigger>
                      <Badge variant="outline" className="font-normal">
                        Not used
                      </Badge>
                    </TooltipTrigger>
                    <TooltipContent side="left">
                      Your own {u.provider} key below is used instead of this system key.
                    </TooltipContent>
                  </Tooltip>
                ) : (
                  <Badge variant="secondary" className="font-normal">
                    Shared
                  </Badge>
                )}
              </TableCell>
              <TableCell />
            </TableRow>
          );
        })}
        {keys.map((key) => {
          const keyUsage = usageByRef.get(`user-${key.id}`);
          return (
            <TableRow key={key.id}>
              <TableCell>
                <Badge variant="outline" className="capitalize font-normal">
                  {key.provider}
                </Badge>
              </TableCell>
              <TableCell className="font-medium">{key.label}</TableCell>
              <TableCell className="font-mono text-xs text-muted-foreground">{key.masked_key}</TableCell>
              <TableCell>
                <UsageCell usage={keyUsage} />
              </TableCell>
              <TableCell>
                <RemainingCell usage={keyUsage} />
              </TableCell>
              <TableCell className="text-xs text-muted-foreground">
                {formatDistanceToNow(new Date(key.created_at), { addSuffix: true })}
              </TableCell>
              <TableCell>
                <Switch checked={key.is_active} onCheckedChange={() => handleToggle(key.id)} />
              </TableCell>
              <TableCell className="text-right">
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-8 text-muted-foreground hover:text-destructive"
                  onClick={() => handleDelete(key.id)}
                >
                  <Trash2 className="size-4" />
                </Button>
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
