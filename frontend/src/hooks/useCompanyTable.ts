"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useMemo, useRef } from "react";

import { api } from "@/lib/api";

const PAGE_SIZE = 50;
const ROW_HEIGHT = 52;

export function useCompanyTable(projectId: string, token: string | null, statusFilter?: string) {
  const parentRef = useRef<HTMLDivElement | null>(null);

  const query = useInfiniteQuery({
    queryKey: ["companies", projectId, statusFilter],
    queryFn: ({ pageParam }) =>
      api.listCompanies(token as string, {
        projectId,
        cursor: pageParam as string | undefined,
        limit: PAGE_SIZE,
        status: statusFilter,
      }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    enabled: !!token,
    // Deliberately unbounded (no `maxPages`): a sliding window that evicts
    // earlier pages sounds memory-friendly, but this table has no reverse
    // pagination (no `getPreviousPageParam`), so evicting the front of the
    // list while scrolled deep silently shrinks `rows.length` — which can
    // re-trigger the "fetch next page" effect below (it only compares
    // against `rows.length`) even while the user is scrolling UP, fighting
    // every attempt to scroll back. At 200k rows of small summary objects
    // this is at most tens of MB, which is a much better trade than a
    // virtualized list that fights the user's scroll direction.
    refetchOnWindowFocus: false,
  });

  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.companies) ?? [], [query.data]);
  const total = query.data?.pages[0]?.total ?? 0;
  const hasMore = !!query.hasNextPage;

  const virtualizer = useVirtualizer({
    count: hasMore ? rows.length + 1 : rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 10,
  });

  const virtualItems = virtualizer.getVirtualItems();

  // Fetch the next page once the sentinel row scrolls into view.
  const lastItemIndex = virtualItems[virtualItems.length - 1]?.index;
  useEffect(() => {
    if (
      lastItemIndex !== undefined &&
      lastItemIndex >= rows.length - 1 &&
      hasMore &&
      !query.isFetchingNextPage
    ) {
      query.fetchNextPage();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastItemIndex, rows.length, hasMore, query.isFetchingNextPage]);

  return {
    parentRef,
    rows,
    total,
    virtualizer,
    virtualItems,
    isLoading: query.isLoading,
    isFetchingNextPage: query.isFetchingNextPage,
    refetch: query.refetch,
  };
}
