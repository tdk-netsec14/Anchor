"use client";

import { Activity as ActivityIcon, ShieldAlert, Wrench } from "lucide-react";
import { useCallback } from "react";

import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorNotice, Skeleton } from "@/components/ui/feedback";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api-client";
import { formatCost, formatLatency, formatNumber, formatRelative, formatTimestamp } from "@/lib/format";

export default function ActivityPage() {
  const activity = useAsync(useCallback(() => api.activity(), []));

  return (
    <PageBody>
      <PageHeader
        title="Activity"
        description="Every question this backend instance has answered, with the model, provider, latency, token spend and guardrail outcomes recorded at the time."
        actions={
          <Button size="sm" variant="secondary" onClick={activity.reload} disabled={activity.loading}>
            Refresh
          </Button>
        }
      />

      <div className="mt-4 flex items-start gap-2 rounded-lg border border-border bg-surface-2/60 px-3.5 py-2.5">
        <ActivityIcon className="mt-0.5 size-3.5 shrink-0 text-fg-subtle" />
        <p className="text-[12.5px] leading-relaxed text-fg-muted">
          This is the running process&rsquo;s own request log, held in memory by the
          observability layer — the most recent 50 queries. It resets when the backend
          restarts, because nothing is written to a database. The structured log stream
          is the durable record.
        </p>
      </div>

      <Card className="mt-5 overflow-hidden">
        <CardHeader>
          <CardTitle>Recent requests</CardTitle>
        </CardHeader>

        {activity.loading ? (
          <div className="space-y-2 px-5 pb-5">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-3/4" />
          </div>
        ) : activity.error ? (
          <div className="px-5 pb-5">
            <ErrorNotice error={activity.error} onRetry={activity.reload} />
          </div>
        ) : activity.data && activity.data.items.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] border-collapse text-left">
              <thead>
                <tr className="border-y border-border">
                  <Th className="pl-4">Question</Th>
                  <Th>Model</Th>
                  <Th>Latency</Th>
                  <Th>Tokens</Th>
                  <Th>Cost</Th>
                  <Th>Signals</Th>
                  <Th className="pr-4 text-right">When</Th>
                </tr>
              </thead>
              <tbody>
                {activity.data.items.map((item) => (
                  <tr key={item.id} className="border-b border-border last:border-0">
                    <td className="max-w-[280px] py-3 pl-4 pr-4">
                      <p className="truncate text-[13px]">{item.query || "—"}</p>
                      <p className="mt-0.5 truncate font-mono text-[10.5px] text-fg-subtle">
                        {item.id}
                      </p>
                    </td>
                    <td className="py-3 pr-4">
                      <p className="font-mono text-[12px]">{item.model}</p>
                      <p className="font-mono text-[10.5px] text-fg-subtle">{item.provider}</p>
                    </td>
                    <td className="py-3 pr-4 text-[13px] text-fg-muted">
                      {formatLatency(item.latency_ms)}
                    </td>
                    <td className="py-3 pr-4 text-[13px] text-fg-muted">
                      {formatNumber(item.tokens_used)}
                    </td>
                    <td className="py-3 pr-4 text-[13px] text-fg-muted">
                      {formatCost(item.cost_usd)}
                    </td>
                    <td className="py-3 pr-4">
                      <span className="flex flex-wrap items-center gap-1">
                        {item.tool_calls.length > 0 ? (
                          <Badge tone="accent">
                            <Wrench className="size-3" />
                            {item.tool_calls.join(", ")}
                          </Badge>
                        ) : null}
                        {item.guardrail_flags.length > 0 ? (
                          <Badge tone="warning">
                            <ShieldAlert className="size-3" />
                            {item.guardrail_flags.length}
                          </Badge>
                        ) : null}
                        {item.sources_count > 0 ? (
                          <Badge>{item.sources_count} src</Badge>
                        ) : null}
                        {item.tool_calls.length === 0 &&
                        item.guardrail_flags.length === 0 &&
                        item.sources_count === 0 ? (
                          <span className="text-[12px] text-fg-subtle">—</span>
                        ) : null}
                      </span>
                    </td>
                    <td
                      className="py-3 pr-4 text-right text-[12px] text-fg-muted"
                      title={formatTimestamp(item.timestamp)}
                    >
                      {formatRelative(item.timestamp)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            icon={<ActivityIcon className="size-6" />}
            title="No requests recorded yet"
            description="Ask a question in the Assistant and the query, the model that served it and its cost will appear here immediately."
          />
        )}
      </Card>
    </PageBody>
  );
}

function Th({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <th
      className={`py-2 text-[11px] font-semibold uppercase tracking-wider text-fg-subtle ${className}`}
    >
      {children}
    </th>
  );
}
