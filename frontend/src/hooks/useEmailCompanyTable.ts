"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useMemo, useRef } from "react";

import { api, type EmailCategory } from "@/lib/api";

const PAGE_SIZE = 50;
const ROW_HEIGHT = 52;

/** Same shape/rationale as useCompanyTable.ts (product-generation table) -
 * see that file's comments for why pagination is deliberately unbounded.
 * `categoryFilter` is applied server-side (see routers/email_finder.py's
 * _CATEGORY_FILTERS) - essential at real scale (tens of thousands of
 * companies per batch), where filtering client-side would mean fetching
 * every page first. */
export function useEmailCompanyTable(
  batchId: string,
  token: string | null,
  statusFilter?: string,
  categoryFilter?: EmailCategory
) {
  const parentRef = useRef<HTMLDivElement | null>(null);

  const query = useInfiniteQuery({
    queryKey: ["email-companies", batchId, statusFilter, categoryFilter],
    queryFn: ({ pageParam }) =>
      api.listEmailCompanies(token as string, {
        batchId,
        cursor: pageParam as string | undefined,
        limit: PAGE_SIZE,
        status: statusFilter,
        category: categoryFilter,
      }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    enabled: !!token,
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
