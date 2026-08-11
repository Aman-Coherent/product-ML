"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ShieldCheck } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { api } from "@/lib/api";

export function CircuitBreakerIndicator({ token, active }: { token: string | null; active: boolean }) {
  const { data } = useQuery({
    queryKey: ["circuit-status"],
    queryFn: () => api.getCircuitStatus(token as string),
    enabled: !!token && active,
    refetchInterval: active ? 10_000 : false,
  });

  if (!data || data.state === "CLOSED") {
    return (
      <Tooltip>
        <TooltipTrigger
          render={
            <Badge variant="outline" className="gap-1 text-muted-foreground font-normal">
              <ShieldCheck className="size-3" /> Jina Reader OK
            </Badge>
          }
        />
        <TooltipContent>Website reading is healthy — no fallback needed.</TooltipContent>
      </Tooltip>
    );
  }

  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Badge variant="outline" className="gap-1 bg-amber-500/15 text-amber-500 border-amber-500/30">
            <AlertTriangle className="size-3" /> Jina degraded — using AI Browse fallback
          </Badge>
        }
      />
      <TooltipContent>
        Jina Reader failed {data.failures} times in a row. Automatically routing website reads through
        Groq compound-beta for the next {Math.ceil(data.reset_in_s)}s.
      </TooltipContent>
    </Tooltip>
  );
}
