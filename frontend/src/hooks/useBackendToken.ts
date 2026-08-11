"use client";

import { useQuery } from "@tanstack/react-query";

async function fetchToken(): Promise<string> {
  const res = await fetch("/api/backend-token");
  if (!res.ok) throw new Error("Failed to obtain backend session token");
  const data = await res.json();
  return data.token as string;
}

/** Caches the short-lived backend JWT and refreshes it well before it expires. */
export function useBackendToken() {
  return useQuery({
    queryKey: ["backend-token"],
    queryFn: fetchToken,
    staleTime: 50 * 60 * 1000, // token is valid 1h, refresh at 50m
    refetchInterval: 50 * 60 * 1000,
    retry: 2,
  });
}
