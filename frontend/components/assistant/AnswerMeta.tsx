"use client";

import { ChevronDown } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { cn, formatCost, formatLatency, formatNumber } from "@/lib/format";
import { QueryResponse } from "@/types/api";

/**
 * The per-answer telemetry strip. Every value shown here comes straight off the
 * query response — nothing is estimated in the browser.
 */
export function AnswerMeta({ response }: { response: QueryResponse }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="mt-3 border-t border-border pt-2.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[11.5px] text-fg-subtle">
        <span className="font-mono text-fg-muted">
          {response.model_used}
          {response.provider ? ` · ${response.provider}` : ""}
        </span>
        <Dot />
        <span>{formatLatency(response.latency_ms)}</span>
        <Dot />
        <span>{formatNumber(response.tokens_used)} tokens</span>
        <Dot />
        <span>{formatCost(response.estimated_cost_usd)}</span>
        {response.fallbacks.length > 0 ? (
          <>
            <Dot />
            <span className="text-warning">
              {response.fallbacks.length} fallback
              {response.fallbacks.length === 1 ? "" : "s"}
            </span>
          </>
        ) : null}

        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="ml-auto inline-flex items-center gap-1 rounded px-1 py-0.5 transition-colors hover:text-fg-muted"
        >
          details
          <ChevronDown className={cn("size-3 transition-transform", open && "rotate-180")} />
        </button>
      </div>

      {open ? (
        <dl className="mt-2.5 grid grid-cols-2 gap-x-6 gap-y-1.5 rounded-lg bg-surface-2 px-3 py-2.5 text-[12px] sm:grid-cols-3">
          <Row label="request" value={response.request_id} mono />
          <Row label="routing" value={response.routing_reason || "—"} />
          <Row label="provider" value={response.provider || "—"} />
          <Row label="prompt tokens" value={formatNumber(response.prompt_tokens)} />
          <Row label="completion tokens" value={formatNumber(response.completion_tokens)} />
          <Row label="sources" value={formatNumber(response.sources.length)} />
          {response.fallbacks.map((f, i) => (
            <Row key={`${f.provider}-${i}`} label={`fallback ${i + 1}`} value={f.reason || f.provider} />
          ))}
        </dl>
      ) : null}
    </div>
  );
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="text-[10.5px] uppercase tracking-wider text-fg-subtle">{label}</dt>
      <dd className={cn("truncate text-fg-muted", mono && "font-mono text-[11px]")}>{value}</dd>
    </div>
  );
}

function Dot() {
  return <span className="text-border-strong">·</span>;
}

export function GuardrailNotice({ flags }: { flags: string[] }) {
  if (flags.length === 0) return null;
  return (
    <div className="mt-3 flex flex-wrap items-center gap-1.5 rounded-lg border border-warning/25 bg-warning-soft/50 px-3 py-2">
      <span className="text-[12px] font-medium text-warning">Output guardrails applied</span>
      {flags.map((flag) => (
        <Badge key={flag} tone="warning">
          {flag}
        </Badge>
      ))}
    </div>
  );
}
