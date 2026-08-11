"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2, Package } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { UrlSourceBadge } from "@/components/projects/UrlSourceBadge";
import { useCompanyTable } from "@/hooks/useCompanyTable";
import { api } from "@/lib/api";
import type { Company } from "@/lib/api";
import { cn } from "@/lib/utils";

const COLUMNS = [
  { key: "company_name", label: "Company", width: "w-56" },
  { key: "location", label: "Location", width: "w-40" },
  { key: "status", label: "Status", width: "w-24" },
  { key: "url_source", label: "URL Source", width: "w-36" },
  { key: "classification", label: "Classification", width: "w-56" },
  { key: "products", label: "Products", width: "w-24" },
];

const STATUS_DOT: Record<string, string> = {
  pending: "bg-muted-foreground/40",
  running: "bg-blue-500 animate-pulse",
  done: "bg-emerald-500",
  failed: "bg-red-500",
};

export function CompanyTable({
  projectId,
  token,
  statusFilter,
}: {
  projectId: string;
  token: string | null;
  statusFilter?: string;
}) {
  const { parentRef, rows, total, virtualizer, virtualItems, isLoading } = useCompanyTable(
    projectId,
    token,
    statusFilter
  );
  const [selected, setSelected] = useState<Company | null>(null);

  return (
    <div className="rounded-lg border">
      <div className="flex items-center justify-between border-b bg-muted/40 px-3 py-2">
        {COLUMNS.map((col) => (
          <div key={col.key} className={cn("text-xs font-medium text-muted-foreground", col.width)}>
            {col.label}
          </div>
        ))}
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-16 text-muted-foreground">
          <Loader2 className="size-5 animate-spin" />
        </div>
      ) : rows.length === 0 ? (
        <div className="flex items-center justify-center py-16 text-sm text-muted-foreground">
          No companies to show.
        </div>
      ) : (
        <div ref={parentRef} className="h-[520px] overflow-auto">
          <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
            {virtualItems.map((virtualRow) => {
              const company = rows[virtualRow.index];
              if (!company) {
                return (
                  <div
                    key={virtualRow.key}
                    style={{
                      position: "absolute",
                      top: 0,
                      left: 0,
                      width: "100%",
                      height: virtualRow.size,
                      transform: `translateY(${virtualRow.start}px)`,
                    }}
                    className="flex items-center justify-center text-xs text-muted-foreground"
                  >
                    Loading more...
                  </div>
                );
              }

              return (
                <div
                  key={virtualRow.key}
                  style={{
                    position: "absolute",
                    top: 0,
                    left: 0,
                    width: "100%",
                    height: virtualRow.size,
                    transform: `translateY(${virtualRow.start}px)`,
                  }}
                  className="flex cursor-pointer items-center border-b px-3 hover:bg-muted/40"
                  onClick={() => setSelected(company)}
                >
                  <div className="w-56 truncate pr-2 text-sm font-medium">{company.company_name}</div>
                  <div className="w-40 truncate pr-2 text-xs text-muted-foreground">
                    {company.location || "—"}
                  </div>
                  <div className="w-24 flex items-center gap-1.5">
                    <span className={cn("size-2 rounded-full", STATUS_DOT[company.status])} />
                    <span className="text-xs capitalize text-muted-foreground">{company.status}</span>
                  </div>
                  <div className="w-36">
                    <UrlSourceBadge source={company.url_read_source} />
                  </div>
                  <div className="w-56 truncate pr-2">
                    {company.display_label ? (
                      <Badge variant="secondary" className="font-normal">
                        {company.display_label}
                      </Badge>
                    ) : (
                      <span className="text-xs text-muted-foreground">—</span>
                    )}
                  </div>
                  <div className="w-24 flex items-center gap-1 text-xs text-muted-foreground">
                    <Package className="size-3.5" />
                    {company.products_count || 0}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div className="border-t px-3 py-2 text-xs text-muted-foreground">
        Showing {rows.length.toLocaleString()} of {total.toLocaleString()} companies
      </div>

      <CompanyDetailSheet company={selected} token={token} onClose={() => setSelected(null)} />
    </div>
  );
}

function CompanyDetailSheet({
  company,
  token,
  onClose,
}: {
  company: Company | null;
  token: string | null;
  onClose: () => void;
}) {
  const { data, isLoading } = useQuery({
    queryKey: ["company-products", company?.id],
    queryFn: () => api.getCompanyProducts(token as string, company!.id),
    enabled: !!company && !!token,
  });

  return (
    <Sheet open={!!company} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full sm:max-w-lg overflow-y-auto">
        <SheetHeader>
          <SheetTitle>{company?.company_name}</SheetTitle>
          <SheetDescription>
            {company?.location} {company?.url ? `· ${company.url}` : ""}
          </SheetDescription>
        </SheetHeader>

        <div className="space-y-4 px-4 pb-6">
          {company?.error_message && (
            <div className="rounded-md border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-500">
              {company.error_message}
            </div>
          )}

          {company?.display_label && (
            <div>
              <p className="text-xs font-medium text-muted-foreground mb-1">Classification</p>
              <Badge variant="secondary">{company.display_label}</Badge>
              {company.classification_confidence != null && (
                <span className="ml-2 text-xs text-muted-foreground">
                  {Math.round(company.classification_confidence * 100)}% confidence
                </span>
              )}
            </div>
          )}

          <div>
            <p className="text-xs font-medium text-muted-foreground mb-1">Website read source</p>
            <UrlSourceBadge source={company?.url_read_source ?? null} />
          </div>

          <div>
            <p className="text-xs font-medium text-muted-foreground mb-2">
              Products ({data?.products.length ?? 0})
            </p>
            {isLoading ? (
              <Loader2 className="size-4 animate-spin text-muted-foreground" />
            ) : data && data.products.length > 0 ? (
              <div className="space-y-1.5">
                {data.products.map((p, i) => (
                  <div key={i} className="flex items-center justify-between gap-2 rounded-md border px-3 py-2">
                    <p className="text-sm font-medium truncate">{p.name}</p>
                    {p.category && (
                      <Badge variant="outline" className="shrink-0 text-xs font-normal">
                        {p.category}
                      </Badge>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">No products generated for this company.</p>
            )}
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}
