"use client";

import { BarChart3, Coins, Cpu, Gauge, Layers, ShieldAlert, Timer, Wrench } from "lucide-react";
import { useCallback } from "react";

import { BarList, SplitBar } from "@/components/analytics/BarList";
import { StatTile, StatTileSkeleton } from "@/components/analytics/StatTile";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorNotice } from "@/components/ui/feedback";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api-client";
import { formatCost, formatDuration, formatLatency, formatNumber } from "@/lib/format";

export default function AnalyticsPage() {
  const metrics = useAsync(useCallback(() => api.metrics(), []));

  return (
    <PageBody>
      <PageHeader
        title="Analytics"
        description="Live counters from the backend's observability layer. Every figure is what this running instance has actually recorded — nothing is estimated, sampled or back-filled."
        actions={
          <Button size="sm" variant="secondary" onClick={metrics.reload} disabled={metrics.loading}>
            Refresh
          </Button>
        }
      />

      {metrics.loading ? (
        <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <StatTileSkeleton key={i} />
          ))}
        </div>
      ) : metrics.error ? (
        <ErrorNotice error={metrics.error} onRetry={metrics.reload} className="mt-6" />
      ) : metrics.data ? (
        <>
          <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatTile
              label="Queries"
              value={formatNumber(metrics.data.total_queries)}
              hint={`${formatNumber(metrics.data.total_requests)} total requests`}
              icon={<BarChart3 className="size-3.5" />}
            />
            <StatTile
              label="Average latency"
              value={formatLatency(metrics.data.latency_ms.average)}
              hint={`peak ${formatLatency(metrics.data.latency_ms.max)}`}
              icon={<Timer className="size-3.5" />}
            />
            <StatTile
              label="Total tokens"
              value={formatNumber(metrics.data.tokens.total)}
              hint={`${formatNumber(metrics.data.tokens.prompt)} in · ${formatNumber(
                metrics.data.tokens.completion,
              )} out`}
              icon={<Cpu className="size-3.5" />}
            />
            <StatTile
              label="Estimated cost"
              value={formatCost(metrics.data.cost.total_usd)}
              hint={
                metrics.data.total_queries > 0
                  ? `${formatCost(metrics.data.cost.average_usd_per_query)} per query`
                  : "no queries yet"
              }
              icon={<Coins className="size-3.5" />}
              accent
            />
          </div>

          <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatTile
              label="Tool calls"
              value={formatNumber(metrics.data.tool_calls.total)}
              hint="across the tool registry"
              icon={<Wrench className="size-3.5" />}
            />
            <StatTile
              label="Guardrail events"
              value={formatNumber(metrics.data.total_guardrail_blocks)}
              hint="outputs modified or blocked"
              icon={<ShieldAlert className="size-3.5" />}
            />
            <StatTile
              label="Provider fallbacks"
              value={formatNumber(metrics.data.total_fallbacks)}
              hint="routed past the first choice"
              icon={<Gauge className="size-3.5" />}
            />
            <StatTile
              label="Indexed chunks"
              value={formatNumber(metrics.data.ingestion.chunks)}
              hint={`${plural(metrics.data.ingestion.documents, "document")}`}
              icon={<Layers className="size-3.5" />}
            />
          </div>

          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Model usage</CardTitle>
              </CardHeader>
              <CardBody>
                <BarList
                  data={metrics.data.requests_by_model}
                  emptyTitle="No model usage yet"
                  emptyDescription="Each question is routed to a tier-appropriate model; the distribution appears here once one has been answered."
                />
              </CardBody>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Provider usage</CardTitle>
              </CardHeader>
              <CardBody>
                <BarList
                  data={metrics.data.requests_by_provider}
                  emptyTitle="No provider traffic yet"
                  emptyDescription="Anchor prefers configured providers in order and falls back automatically when one cannot serve a request."
                />
              </CardBody>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Tool calls by name</CardTitle>
              </CardHeader>
              <CardBody>
                <BarList
                  data={metrics.data.tool_calls.by_name}
                  emptyTitle="No tools invoked yet"
                  emptyDescription="Ask a question that needs a calculation, a knowledge-base search or a support ticket and it will show up here."
                />
              </CardBody>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Guardrail events by rule</CardTitle>
              </CardHeader>
              <CardBody>
                <BarList
                  data={metrics.data.guardrail_flags}
                  emptyTitle="Guardrails have not fired"
                  emptyDescription="Nothing has been redacted, blocked or flagged on output so far — which is the expected state for a clean corpus."
                />
              </CardBody>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Token split</CardTitle>
              </CardHeader>
              <CardBody>
                <SplitBar
                  segments={[
                    {
                      label: "Prompt",
                      value: metrics.data.tokens.prompt,
                      color: "var(--chart-1)",
                    },
                    {
                      label: "Completion",
                      value: metrics.data.tokens.completion,
                      color: "var(--chart-2)",
                    },
                  ]}
                />
              </CardBody>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Service</CardTitle>
              </CardHeader>
              <CardBody>
                <dl className="space-y-2 text-[13px]">
                  <Row label="Uptime" value={formatDuration(metrics.data.uptime_seconds)} />
                  <Row
                    label="Error rate"
                    value={`${(metrics.data.error_rate * 100).toFixed(1)}%`}
                    hint={
                      metrics.data.error_rate > 0
                        ? plural(metrics.data.total_errors, "error")
                        : "no errors"
                    }
                  />
                  <Row
                    label="Requests by endpoint"
                    value={Object.keys(metrics.data.requests_by_endpoint).length}
                    hint="distinct endpoints hit"
                  />
                  <Row
                    label="Routing reasons"
                    value={Object.keys(metrics.data.routing_reasons).length}
                    hint="distinct tier decisions"
                  />
                </dl>
              </CardBody>
            </Card>
          </div>

          <p className="mt-5 text-[12px] leading-relaxed text-fg-subtle">
            Counters live in the backend process and reset on restart. They describe
            this instance since it booted {formatDuration(metrics.data.uptime_seconds)} ago.
          </p>
        </>
      ) : null}
    </PageBody>
  );
}

function Row({ label, value, hint }: { label: string; value: string | number; hint?: string }) {  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-fg-muted">{label}</dt>
      <dd className="text-right">
        <span className="font-mono tabular-nums">{value}</span>
        {hint ? <span className="ml-2 text-[11.5px] text-fg-subtle">{hint}</span> : null}
      </dd>
    </div>
  );
}

/** "1 document" / "3 documents" — counters here are routinely zero or one. */
function plural(count: number, noun: string): string {
  return `${formatNumber(count)} ${noun}${count === 1 ? "" : "s"}`;
}
