import { Globe, Bot, MapPin, CircleSlash } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const SOURCE_CONFIG: Record<
  string,
  { label: string; icon: React.ElementType; className: string }
> = {
  jina_reader: {
    label: "Website",
    icon: Globe,
    className: "bg-emerald-500/15 text-emerald-500 border-emerald-500/30",
  },
  compound_beta: {
    label: "AI Browse",
    icon: Bot,
    className: "bg-blue-500/15 text-blue-500 border-blue-500/30",
  },
  name_location: {
    label: "Name/Location",
    icon: MapPin,
    className: "bg-amber-500/15 text-amber-500 border-amber-500/30",
  },
  none: {
    label: "No source",
    icon: CircleSlash,
    className: "bg-muted text-muted-foreground",
  },
};

export function UrlSourceBadge({ source }: { source: string | null }) {
  const config = SOURCE_CONFIG[source ?? "none"] ?? SOURCE_CONFIG.none;
  const Icon = config.icon;
  return (
    <Badge variant="outline" className={cn("gap-1 font-normal", config.className)}>
      <Icon className="size-3" />
      {config.label}
    </Badge>
  );
}
