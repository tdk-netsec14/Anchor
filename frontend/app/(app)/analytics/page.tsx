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
  const overview = useAsync(useCallback(() => api.analyticsOverview(30), []));

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

      {/* Two different questions, deliberately kept apart. The block below is
          this workspace's durable, per-tenant usage, read from recorded rows;
          the tiles after it are the running process's live counters, which
          reset on every deploy. Mixing them would let a restart look like a
          drop in usage. */}
      <Card className="mt-6">
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>This workspace — last 30 days</CardTitle>
          <Button
            size="sm"
            variant="ghost"
            onClick={overview.reload}
            disabled={overview.loading}
          >
            Refresh
          </Button>
        </CardHeader>
        <CardBody>
          {overview.loading ? (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <StatTileSkeleton key={i} />
              ))}
            </div>
          ) : overview.error ? (
            <ErrorNotice error={overview.error} onRetry={overview.reload} />
          ) : overview.data ? (
            <>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <StatTile
                  label="Answered"
                  value={formatNumber(overview.data.requests.total)}
                  hint={`${Math.round(overview.data.requests.success_rate * 100)}% succeeded · ${formatNumber(
                    overview.data.requests.errors,
                  )} errored`}
                  icon={<BarChart3 className="size-3.5" />}
                />
                <StatTile
                  label="Citations"
                  value={formatNumber(overview.data.retrieval.citations_returned)}
                  hint="sources returned to the user"
                  icon={<Layers className="size-3.5" />}
                />
                <StatTile
                  label="Documents"
                  value={formatNumber(overview.data.documents.total)}
                  hint={
                    overview.data.documents.failed
                      ? `${overview.data.documents.failed} failed · ${formatNumber(
                          overview.data.documents.chunks,
                        )} chunks`
                      : `${formatNumber(overview.data.documents.chunks)} indexed chunks`
                  }
                  icon={<Layers className="size-3.5" />}
                />
                <StatTile
                  label="Spend"
                  value={formatCost(overview.data.cost.total_usd)}
                  hint={`${formatNumber(overview.data.tokens.total)} tokens`}
                  icon={<Coins className="size-3.5" />}
                  accent={overview.data.cost.total_usd > 0}
                />
              </div>
              <p className="mt-3 text-[11.5px] leading-relaxed text-fg-subtle">
                Cost is zero until <code className="font-mono">COST_INPUT_PER_MTOK</code>{" "}
                and <code className="font-mono">COST_OUTPUT_PER_MTOK</code> are configured
                for the models in use. Anchor hardcodes no prices and reports 0.0 rather
                than guessing.
              </p>
            </>
          ) : null}
        </CardBody>
      </Card>

      <div className="mt-8 flex items-baseline gap-3">
        <h2 className="text-sm font-semibold tracking-tight">This process</h2>
        <p className="text-[12px] text-fg-subtle">
          Live counters, reset on every restart and shared by every workspace on this
          instance.
        </p>
      </div>

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
