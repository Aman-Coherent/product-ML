import { Badge } from "@/components/ui/badge";
import type { EmailTier, WebsiteSource } from "@/lib/api";

// Simplified to exactly 3 plain-English categories per user request (the
// original 5 backend tier names - scraped_verified/offsite,
// pattern_smtp_verified/catchall/unverified - were causing real confusion
// in the UI even though they're meaningful internally for scoring/ranking).
// The 3 categories combine two backend facts into the one distinction that
// actually matters to someone reading the table:
//   1. Was the EMAIL actually found written on a real page, or guessed?
//   2. If found, was it on the website THEY gave us, or one we had to find
//      ourselves (web search / guessed domain)?
type SimpleCategory = "found_given" | "found_discovered" | "guessed";

function categorize(tier: EmailTier, websiteSource: WebsiteSource | null): SimpleCategory {
  const wasFound = tier === "scraped_verified" || tier === "scraped_offsite";
  if (!wasFound) return "guessed";
  return websiteSource === "provided" ? "found_given" : "found_discovered";
}

const STYLES: Record<SimpleCategory, string> = {
  found_given: "bg-emerald-500/15 text-emerald-500 border-emerald-500/30",
  found_discovered: "bg-blue-500/15 text-blue-500 border-blue-500/30",
  guessed: "bg-amber-500/15 text-amber-600 border-amber-500/30",
};

const LABELS: Record<SimpleCategory, string> = {
  found_given: "Found on given website",
  found_discovered: "Found on website we discovered",
  guessed: "Guessed (not found)",
};

export function EmailTierBadge({
  tier,
  confidence,
  websiteSource,
}: {
  tier: EmailTier | null;
  confidence?: number | null;
  websiteSource?: WebsiteSource | null;
}) {
  if (!tier) return <span className="text-xs text-muted-foreground">—</span>;
  const category = categorize(tier, websiteSource ?? null);
  return (
    <Badge variant="outline" className={STYLES[category]}>
      {LABELS[category]}
      {confidence != null && <span className="ml-1 opacity-70">{Math.round(confidence * 100)}%</span>}
    </Badge>
  );
}
