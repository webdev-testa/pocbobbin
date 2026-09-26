import { FlaskConical, GitPullRequest, Import, Minus, Zap, type LucideIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { LineSwatch } from "@/components/map-parts";
import { TONE_CLASSES } from "@/components/StatusBadge";
import { TierLegend } from "@/components/TierBadge";
import { CATEGORY_ORDER, EDGE_STYLE, type FileCategory } from "@/lib/repo-map";
import type { Tier } from "@/lib/review-report";
import { cn } from "@/lib/utils";

interface CategoryMeta {
  label: string;
  short: string;
  icon: LucideIcon;
  badge: string;
  card: string;
  /** Left accent of a collapsed folder that holds this category. */
  accent: string;
}

// Changed is the strongest accent, impacted a strong warning, importers the same warning outlined.
export const CATEGORY_META: Record<FileCategory, CategoryMeta> = {
  changed: {
    label: "Changed in this PR", short: "changed", icon: GitPullRequest,
    badge: TONE_CLASSES.info, card: "border-2 border-info bg-info-muted", accent: "border-l-info",
  },
  impacted: {
    label: "Impacted — calls changed code", short: "impacted", icon: Zap,
    badge: TONE_CLASSES.warning, card: "border-2 border-warning bg-warning-muted", accent: "border-l-warning",
  },
  imports: {
    label: "Imports a changed file", short: "imports changed", icon: Import,
    badge: "border-dashed border-warning/60 text-warning", card: "border-2 border-dashed border-warning/60", accent: "border-l-warning/60",
  },
  unrelated: {
    label: "Unrelated", short: "unrelated", icon: Minus,
    badge: "text-muted-foreground", card: "border", accent: "border-l-border",
  },
};

export function CategoryBadge({ category, short = false }: { category: FileCategory; short?: boolean }) {
  const meta = CATEGORY_META[category];
  const Icon = meta.icon;
  return (
    <Badge variant="outline" className={meta.badge}>
      <Icon aria-hidden="true" />
      {short ? meta.short : meta.label}
    </Badge>
  );
}

export function TestBadge() {
  return (
    <Badge variant="outline" className="text-muted-foreground">
      <FlaskConical aria-hidden="true" />test
    </Badge>
  );
}

export const fileCount = (count: number) => `${count} file${count === 1 ? "" : "s"}`;

/** "2 changed · 1 impacted" for a folder; unrelated files are counted by the folder's file total. */
export function categoryCounts(counts: Record<FileCategory, number>) {
  const parts = CATEGORY_ORDER.filter((c) => c !== "unrelated" && counts[c]).map((c) => `${counts[c]} ${CATEGORY_META[c].short}`);
  return parts.join(" · ") || "nothing changed or impacted";
}

export function RepoMapLegend({ tiers }: { tiers: Tier[] }) {
  return (
    <div className="space-y-2">
      <ul aria-label="File categories" className="flex flex-wrap items-center gap-2">
        {CATEGORY_ORDER.map((category) => <li key={category}><CategoryBadge category={category} /></li>)}
        <li><TestBadge /></li>
      </ul>
      <ul aria-label="Import edges" className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <LineSwatch label="Between changed or impacted files" lineStyle={EDGE_STYLE.strong} />
        <LineSwatch label="Touching a file that imports a changed one" lineStyle={EDGE_STYLE.medium} />
        <LineSwatch label="Other imports" lineStyle={EDGE_STYLE.weak} />
      </ul>
      <TierLegend tiers={tiers} />
    </div>
  );
}

export const categoryCard = (category: FileCategory) => cn(CATEGORY_META[category].card, category === "unrelated" && "text-muted-foreground");
