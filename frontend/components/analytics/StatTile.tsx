"use client";

import type { ReactNode } from "react";

import { Card, CardBody } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/feedback";
import { cn } from "@/lib/format";

/**
 * A number, not a chart. Where a single figure is the message, drawing axes
 * around it would add ink without adding information.
 *
 * The value uses proportional figures — tabular digits give every character
 * the width of a zero, which looks loose at display sizes.
 */
export function StatTile({
  label,
  value,
  hint,
  icon,
  accent,
}: {
  label: string;
  value: string;
  hint?: string;
  icon?: ReactNode;
  /** Marks a figure worth leading with, e.g. a running cost. */
  accent?: boolean;
}) {
  return (
    <Card>
      <CardBody className="pt-4">
        <div className="flex items-center gap-1.5 text-fg-subtle">
          {icon}
          <span className="text-[11px] font-semibold uppercase tracking-wider">{label}</span>
        </div>
        <p
          className={cn(
            "mt-1.5 text-2xl font-semibold tracking-tight",
            accent ? "text-chart-1" : "text-fg",
          )}
        >
          {value}
        </p>
        {hint ? <p className="mt-0.5 text-[11.5px] leading-snug text-fg-subtle">{hint}</p> : null}
      </CardBody>
    </Card>
  );
}

export function StatTileSkeleton() {
  return (
    <Card>
      <CardBody className="pt-4">
        <Skeleton className="h-3 w-20" />
        <Skeleton className="mt-2.5 h-7 w-16" />
      </CardBody>
    </Card>
  );
}
