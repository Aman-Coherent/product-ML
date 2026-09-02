"use client";

import { useState } from "react";
import { Copy, ExternalLink, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { EmailTierBadge } from "@/components/emails/EmailTierBadge";
import { useEmailCompanyTable } from "@/hooks/useEmailCompanyTable";
import type { EmailCompany } from "@/lib/api";
import { cn } from "@/lib/utils";

const COLUMNS = [
  { key: "company_name", label: "Company", width: "w-48" },
  { key: "location", label: "Location", width: "w-36" },
  { key: "status", label: "Status", width: "w-24" },
  { key: "website", label: "Website", width: "w-40" },
  { key: "email", label: "Primary Email", width: "w-56" },
  { key: "tier", label: "Confidence", width: "w-48" },
];

const STATUS_DOT: Record<string, string> = {
  pending: "bg-muted-foreground/40",
  running: "bg-blue-500 animate-pulse",
  done: "bg-emerald-500",
  failed: "bg-red-500",
};

const WEBSITE_SOURCE_LABELS: Record<string, string> = {
  provided: "Provided",
  web_search: "Found via search",
  domain_guess: "Guessed domain",
  not_found: "Not found",
};

export function EmailCompanyTable({
  batchId,
  token,
  statusFilter,
}: {
  batchId: string;
  token: string | null;
  statusFilter?: string;
}) {
  const { parentRef, rows, total, virtualizer, virtualItems, isLoading } = useEmailCompanyTable(
    batchId,
    token,
    statusFilter
  );
  const [selected, setSelected] = useState<EmailCompany | null>(null);

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
                  <div className="w-48 truncate pr-2 text-sm font-medium">{company.company_name}</div>
                  <div className="w-36 truncate pr-2 text-xs text-muted-foreground">
                    {company.location || "—"}
                  </div>
                  <div className="w-24 flex items-center gap-1.5">
                    <span className={cn("size-2 rounded-full", STATUS_DOT[company.status])} />
                    <span className="text-xs capitalize text-muted-foreground">{company.status}</span>
                  </div>
                  <div className="w-40 truncate pr-2 text-xs text-muted-foreground">
                    {company.website_source ? WEBSITE_SOURCE_LABELS[company.website_source] : "—"}
                  </div>
                  <div className="w-56 truncate pr-2 text-sm">{company.primary_email || "—"}</div>
                  <div className="w-48">
                    <EmailTierBadge tier={company.primary_tier} confidence={company.primary_confidence} />
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

      <CompanyDetailSheet company={selected} onClose={() => setSelected(null)} />
    </div>
  );
}

function copyToClipboard(text: string) {
  navigator.clipboard.writeText(text).then(() => toast.success("Copied to clipboard"));
}

function CompanyDetailSheet({ company, onClose }: { company: EmailCompany | null; onClose: () => void }) {
  return (
    <Sheet open={!!company} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full sm:max-w-lg overflow-y-auto">
        <SheetHeader>
          <SheetTitle>{company?.company_name}</SheetTitle>
          <SheetDescription>
            {company?.location} {company?.resolved_url ? `· ${company.resolved_url}` : ""}
          </SheetDescription>
        </SheetHeader>

        <div className="space-y-4 px-4 pb-6">
          {company?.error_message && (
            <div className="rounded-md border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-600">
              {company.error_message === "website_not_found"
                ? "No official website could be found for this company (no URL provided, web search found nothing, and no matching domain resolved)."
                : company.error_message === "no_email_found"
                ? "A real website was found, but no email address could be found or guessed on it."
                : company.error_message}
            </div>
          )}

          {company?.resolved_url && (
            <div>
              <p className="text-xs font-medium text-muted-foreground mb-1">Website used</p>
              <a
                href={company.resolved_url}
                target="_blank"
                rel="noreferrer"
                className="flex items-center gap-1 text-sm text-primary hover:underline"
              >
                {company.resolved_url} <ExternalLink className="size-3" />
              </a>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {company.website_source ? WEBSITE_SOURCE_LABELS[company.website_source] : "—"}
              </p>
            </div>
          )}

          {company?.primary_email && (
            <div>
              <p className="text-xs font-medium text-muted-foreground mb-1">Primary email</p>
              <div className="flex items-center justify-between gap-2 rounded-md border px-3 py-2">
                <div>
                  <p className="text-sm font-medium">{company.primary_email}</p>
                  <p className="text-xs text-muted-foreground capitalize">{company.primary_label}</p>
                </div>
                <div className="flex items-center gap-2">
                  <EmailTierBadge tier={company.primary_tier} confidence={company.primary_confidence} />
                  <Button variant="ghost" size="icon" className="size-7" onClick={() => copyToClipboard(company.primary_email!)}>
                    <Copy className="size-3.5" />
                  </Button>
                </div>
              </div>
              {company.primary_source_page && (
                <p className="mt-1 text-xs text-muted-foreground truncate">
                  Source: {company.primary_source_page}
                </p>
              )}
            </div>
          )}

          <div>
            <p className="text-xs font-medium text-muted-foreground mb-2">
              Other emails found ({company?.alternate_emails.length ?? 0})
            </p>
            {company && company.alternate_emails.length > 0 ? (
              <div className="space-y-1.5">
                {company.alternate_emails.map((c, i) => (
                  <div key={i} className="flex items-center justify-between gap-2 rounded-md border px-3 py-2">
                    <div className="min-w-0">
                      <p className="text-sm truncate">{c.email}</p>
                      <p className="text-xs text-muted-foreground capitalize">{c.label}</p>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <EmailTierBadge tier={c.tier} confidence={c.confidence} />
                      <Button variant="ghost" size="icon" className="size-7" onClick={() => copyToClipboard(c.email)}>
                        <Copy className="size-3.5" />
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">No other emails found for this company.</p>
            )}
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}
