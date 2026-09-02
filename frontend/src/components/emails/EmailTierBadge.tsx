import { Badge } from "@/components/ui/badge";
import type { EmailTier } from "@/lib/api";

// Worst to best - mirrors backend/core/email_finder/models.py's EmailTier
// exactly, including the ordering. Colors deliberately escalate red -> amber
// -> green so "how much can I trust this" is legible at a glance without
// reading the label - this is the whole point of tiering (see the backend
// pipeline's module docstring): a guessed, unverified address must never
// LOOK as trustworthy as one scraped straight off the company's own site.
const TIER_STYLES: Record<EmailTier, string> = {
  pattern_unverified: "bg-red-500/15 text-red-500 border-red-500/30",
  pattern_catchall: "bg-amber-500/15 text-amber-500 border-amber-500/30",
  pattern_smtp_verified: "bg-amber-500/15 text-amber-600 border-amber-500/30",
  scraped_offsite: "bg-blue-500/15 text-blue-500 border-blue-500/30",
  scraped_verified: "bg-emerald-500/15 text-emerald-500 border-emerald-500/30",
};

const TIER_LABELS: Record<EmailTier, string> = {
  pattern_unverified: "Guessed (unverified)",
  pattern_catchall: "Guessed (domain accepts all)",
  pattern_smtp_verified: "Guessed (mail server confirmed)",
  scraped_offsite: "Found on site (other domain)",
  scraped_verified: "Found on official site",
};

export function EmailTierBadge({ tier, confidence }: { tier: EmailTier | null; confidence?: number | null }) {
  if (!tier) return <span className="text-xs text-muted-foreground">—</span>;
  return (
    <Badge variant="outline" className={TIER_STYLES[tier]}>
      {TIER_LABELS[tier]}
      {confidence != null && <span className="ml-1 opacity-70">{Math.round(confidence * 100)}%</span>}
    </Badge>
  );
}
