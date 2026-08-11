"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Info } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { AddKeyDialog } from "@/components/settings/AddKeyDialog";
import { ApiKeyTable } from "@/components/settings/ApiKeyTable";
import { UsageOverview } from "@/components/settings/UsageOverview";
import { useBackendToken } from "@/hooks/useBackendToken";
import { api } from "@/lib/api";

export default function SettingsPage() {
  const { data: token } = useBackendToken();
  const queryClient = useQueryClient();

  const { data: keys, isLoading } = useQuery({
    queryKey: ["api-keys"],
    queryFn: () => api.listApiKeys(token as string),
    enabled: !!token,
  });

  const { data: usage } = useQuery({
    queryKey: ["api-key-usage"],
    queryFn: () => api.getKeyUsage(token as string),
    enabled: !!token,
    refetchInterval: 30_000, // informational, not job-critical - a light poll is enough
  });

  const { data: usageSummary, isLoading: isSummaryLoading } = useQuery({
    queryKey: ["api-key-usage-summary"],
    queryFn: () => api.getUsageSummary(token as string),
    enabled: !!token,
    refetchInterval: 30_000,
  });

  function refetchKeys() {
    queryClient.invalidateQueries({ queryKey: ["api-keys"] });
    queryClient.invalidateQueries({ queryKey: ["api-key-usage"] });
    queryClient.invalidateQueries({ queryKey: ["api-key-usage-summary"] });
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Manage the LLM API keys used for classification and product generation.
        </p>
      </div>

      <Alert>
        <Info className="size-4" />
        <AlertTitle>Your own key is always used first</AlertTitle>
        <AlertDescription>
          A shared system pool of Groq and Mistral keys is used by default so you can run jobs
          immediately. The moment you add your own Groq or Mistral key below, it fully replaces
          the system pool for your jobs — the shared keys are no longer used at all. Add Claude or
          OpenAI as an optional quality fallback tier.
        </AlertDescription>
      </Alert>

      <UsageOverview summary={usageSummary} isLoading={isSummaryLoading} />

      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <div>
            <CardTitle>API Keys</CardTitle>
            <CardDescription>Encrypted at rest. Never shared across users.</CardDescription>
          </div>
          <AddKeyDialog token={token ?? null} onAdded={refetchKeys} />
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <p className="text-sm text-muted-foreground">Loading...</p>
          ) : (
            <ApiKeyTable keys={keys ?? []} usage={usage} token={token ?? null} onChanged={refetchKeys} />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
