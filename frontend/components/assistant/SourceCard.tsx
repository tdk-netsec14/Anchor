"use client";

import { ChevronDown, FileText } from "lucide-react";
import { useState } from "react";

import { cn } from "@/lib/format";
import { SourceDetail } from "@/types/api";

/**
 * One citation. `source_details` carries the page, score and excerpt the
 * retriever already had; a citation surfaced later by the `search_kb` tool
 * arrives as a bare string, so it renders without an excerpt rather than
 * showing an empty panel.
 */
export function SourceCard({ detail }: { detail: SourceDetail }) {
  const [open, setOpen] = useState(false);
  const hasExcerpt = detail.excerpt.length > 0;

  return (
    <div className="rounded-lg border border-border bg-surface">
      <div className="flex items-start gap-2.5 p-2.5">
        <FileText className="mt-0.5 size-3.5 shrink-0 text-fg-subtle" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-[13px] font-medium">{detail.doc_name}</p>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[11.5px] text-fg-subtle">
            {detail.page_number != null ? <span>page {detail.page_number}</span> : null}
            <span className="font-mono">relevance {detail.score.toFixed(2)}</span>
            <span className="font-mono truncate opacity-70">{detail.chunk_id}</span>
          </p>

          {hasExcerpt ? (
            <>
              <p
                className={cn(
                  "mt-2 text-[12.5px] leading-relaxed text-fg-muted",
                  !open && "line-clamp-2",
                )}
              >
                {detail.excerpt}
              </p>
              {detail.excerpt.length > 120 ? (
                <button
                  type="button"
                  onClick={() => setOpen((v) => !v)}
                  className="mt-1 inline-flex items-center gap-1 text-[11.5px] font-medium text-accent hover:text-accent-hover"
                >
                  {open ? "Show less" : "Show more"}
                  <ChevronDown className={cn("size-3 transition-transform", open && "rotate-180")} />
                </button>
              ) : null}
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}

/** Fallback rendering for a citation that carries no structured detail. */
export function PlainCitation({ citation }: { citation: string }) {
  return (
    <div className="flex items-start gap-2.5 rounded-lg border border-border bg-surface p-2.5">
      <FileText className="mt-0.5 size-3.5 shrink-0 text-fg-subtle" />
      <p className="font-mono text-[12px] text-fg-muted">{citation}</p>
    </div>
  );
}
