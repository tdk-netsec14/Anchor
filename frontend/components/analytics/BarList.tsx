"use client";

import { useState } from "react";

import { EmptyState } from "@/components/ui/feedback";
import { cn, formatNumber } from "@/lib/format";

type Entry = { label: string; value: number };

/**
 * Horizontal bars for a single measure across a handful of named categories.
 *
 * One series means one colour — the category is already carried by the row
 * label, so a categorical palette would be decoration, and the title names
 * what is plotted, which is why there is no legend. Values are direct-labelled
 * at the tip because with no axis, the label is the only way to read a number.
 */
export function BarList({
  data,
  formatValue = formatNumber,
  emptyTitle,
  emptyDescription,
}: {
  data: Record<string, number>;
  formatValue?: (value: number) => string;
  emptyTitle: string;
  emptyDescription: string;
}) {
  const [hovered, setHovered] = useState<string | null>(null);

  const entries: Entry[] = Object.entries(data)
    .filter(([, value]) => Number.isFinite(value))
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8)
    .map(([label, value]) => ({ label, value }));

  if (entries.length === 0 || entries.every((e) => e.value === 0)) {
    return <EmptyState title={emptyTitle} description={emptyDescription} />;
  }

  const max = Math.max(...entries.map((e) => e.value));

  return (
    <ul className="space-y-2.5">
      {entries.map((entry) => {
        const pct = max > 0 ? (entry.value / max) * 100 : 0;
        return (
          <li
            key={entry.label}
            className="group"
            onMouseEnter={() => setHovered(entry.label)}
            onMouseLeave={() => setHovered(null)}
          >
            <div className="mb-1 flex items-baseline justify-between gap-3">
              <span
                className={cn(
                  "truncate text-[12.5px]",
                  hovered === entry.label ? "text-fg" : "text-fg-muted",
                )}
              >
                {entry.label}
              </span>
              <span className="shrink-0 font-mono text-[11.5px] tabular-nums text-fg-subtle">
                {formatValue(entry.value)}
              </span>
            </div>
            {/* The track is a surface step, not a border: it shows the scale
                without competing with the mark. */}
            <div className="h-2 w-full overflow-hidden rounded-full bg-surface-2">
              <div
                className="h-full rounded-full bg-chart-1 transition-[width] duration-500 ease-out"
                style={{ width: `${Math.max(pct, 1.5)}%` }}
              />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

/**
 * A single stacked bar for a two-part whole (prompt vs completion tokens).
 * Two series, so a legend is present and the segments are separated by a 2px
 * surface gap rather than a stroke.
 */
export function SplitBar({
  segments,
}: {
  segments: { label: string; value: number; color: string }[];
}) {
  const [hovered, setHovered] = useState<string | null>(null);
  const total = segments.reduce((sum, s) => sum + s.value, 0);
  const present = segments.filter((s) => s.value > 0);

  if (total === 0) {
    return <EmptyState title="No tokens yet" description="Token counts appear once the assistant has answered a question." />;
  }

  return (
    <div>
      <div className="flex h-2.5 w-full gap-0.5 overflow-hidden rounded-full">
        {present.map((segment) => (
          <div
            key={segment.label}
            style={{ width: `${(segment.value / total) * 100}%`, backgroundColor: segment.color }}
            onMouseEnter={() => setHovered(segment.label)}
            onMouseLeave={() => setHovered(null)}
            className="h-full transition-opacity"
          />
        ))}
      </div>

      <ul className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5">
        {segments.map((segment) => (
          <li
            key={segment.label}
            className="flex items-center gap-1.5"
            onMouseEnter={() => setHovered(segment.label)}
            onMouseLeave={() => setHovered(null)}
          >
            <span
              className="size-2 shrink-0 rounded-[2px]"
              style={{ backgroundColor: segment.color }}
              aria-hidden="true"
            />
            <span className="text-[12px] text-fg-muted">
              {segment.label}
              <span className="ml-1.5 font-mono tabular-nums text-fg-subtle">
                {hovered === segment.label
                  ? formatNumber(segment.value)
                  : `${Math.round((segment.value / total) * 100)}%`}
              </span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
