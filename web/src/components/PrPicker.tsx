import { GitPullRequest } from "lucide-react";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { ReportIndexEntry } from "@/lib/report-source";
import { shortSha } from "@/lib/review-report";

function formatDate(iso: string) {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** "PR #24 · <title> · Sep 26 · 845737e"; the title is left out when it only repeats "PR #24". */
export function prLabel(entry: ReportIndexEntry) {
  const title = entry.title === `PR #${entry.pr}` ? [] : [entry.title];
  return [`PR #${entry.pr}`, ...title, formatDate(entry.generated_at), shortSha(entry.head_sha)].join(" · ");
}

interface PrPickerProps {
  entries: ReportIndexEntry[];
  selected: number | null;
  onSelect: (pr: number) => void;
}

export function PrPicker({ entries, selected, onSelect }: PrPickerProps) {
  return (
    <Select value={selected === null ? undefined : String(selected)} onValueChange={(value) => onSelect(Number(value))}>
      <SelectTrigger aria-label="Pull request to review" className="w-full min-w-0 sm:w-auto sm:max-w-80">
        <GitPullRequest aria-hidden="true" className="text-muted-foreground" />
        <SelectValue placeholder="Choose a PR" />
      </SelectTrigger>
      <SelectContent>
        {entries.map((entry, index) => (
          <SelectItem key={entry.pr} value={String(entry.pr)}>
            {prLabel(entry)}
            {index === 0 ? <span className="text-xs text-muted-foreground">latest</span> : null}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
