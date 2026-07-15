"use client";

import { ChevronRight, Wrench } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { cn, formatLatency } from "@/lib/format";
import { ToolCallRecord } from "@/types/api";

/** Tools the agent invoked, with the arguments and truncated output it saw. */
export function ToolCallList({ calls }: { calls: ToolCallRecord[] }) {
  if (calls.length === 0) return null;

  return (
    <div className="mt-3 space-y-1.5">
      {calls.map((call, index) => (
        <ToolCallRow key={`${call.name}-${index}`} call={call} />
      ))}
    </div>
  );
}

function ToolCallRow({ call }: { call: ToolCallRecord }) {
  const [open, setOpen] = useState(false);
  const args = JSON.stringify(call.arguments, null, 2);

  return (
    <div className="rounded-lg border border-border bg-surface-2/60">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left"
      >
        <ChevronRight
          className={cn("size-3 shrink-0 text-fg-subtle transition-transform", open && "rotate-90")}
        />
        <Wrench className="size-3 shrink-0 text-fg-subtle" />
        <span className="font-mono text-[12px] font-medium">{call.name}</span>
        {!call.ok ? <Badge tone="danger">failed</Badge> : null}
        <span className="ml-auto font-mono text-[11px] text-fg-subtle">
          {formatLatency(call.latency_ms)}
        </span>
      </button>

      {open ? (
        <div className="space-y-2 border-t border-border px-3 py-2">
          <div>
            <p className="mb-1 text-[10.5px] uppercase tracking-wider text-fg-subtle">arguments</p>
            <pre className="overflow-x-auto font-mono text-[11.5px] leading-relaxed text-fg-muted">
              {args}
            </pre>
          </div>
          {call.result_preview ? (
            <div>
              <p className="mb-1 text-[10.5px] uppercase tracking-wider text-fg-subtle">
                result
              </p>
              <pre className="overflow-x-auto whitespace-pre-wrap font-mono text-[11.5px] leading-relaxed text-fg-muted">
                {call.result_preview}
              </pre>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
