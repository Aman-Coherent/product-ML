"use client";

import { Info } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { ProviderUsageSummary } from "@/lib/api";

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
}

const PROVIDER_LABELS: Record<string, string> = {
  groq: "Groq",
  mistral: "Mistral",
};

export function UsageOverview({ summary, isLoading }: { summary: ProviderUsageSummary[] | undefined; isLoading: boolean }) {
  if (isLoading) {
    return <p className="text-sm text-muted-foreground">Loading usage...</p>;
  }

  if (!summary || summary.length === 0) {
    return null;
  }

  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2">
        {summary.map((s) => (
          <Card key={s.provider}>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">{PROVIDER_LABELS[s.provider] ?? s.provider}</CardTitle>
              <CardDescription>
                {s.key_count} key{s.key_count === 1 ? "" : "s"} in rotation, each fanned out across every free model
              </CardDescription>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <p className="text-xs text-muted-foreground">Today</p>
                <p className="font-medium">
                  {formatNumber(s.requests_today)} req &middot; {formatNumber(s.tokens_today)} tok
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Last 30 days</p>
                <p className="font-medium">
                  {formatNumber(s.requests_month)} req &middot; {formatNumber(s.tokens_month)} tok
                </p>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
      <Alert>
        <Info className="size-4" />
        <AlertTitle>Limits are per model, per key, not shared</AlertTitle>
        <AlertDescription>
          Groq and Mistral both rate-limit per model. Each key below is automatically fanned out across
          several free models, so &quot;remaining today&quot; in the table is per-model, not one shared
          number for the whole key. If two of your keys were generated from the same underlying
          account, they share the same organization-level quota.
        </AlertDescription>
      </Alert>
    </div>
  );
}
