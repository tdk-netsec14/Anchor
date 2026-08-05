"use client";

import { Activity, ArrowUpRight, BookOpen, Bot, Clock, Users, Wallet } from "lucide-react";
import Link from "next/link";
import { useCallback } from "react";

import { BarList } from "@/components/analytics/BarList";
import { StatTile, StatTileSkeleton } from "@/components/analytics/StatTile";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { buttonClass } from "@/components/ui/button";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorNotice } from "@/components/ui/feedback";
import { useAuth } from "@/hooks/useAuth";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api-client";
import { formatCost, formatNumber } from "@/lib/format";

/**
 * The signed-in landing page.
 *
 * Every number is read from what the backend recorded for this workspace
 * (`/analytics/overview`), not from the process-local counters the older
 * dashboard used — those reset on every deploy and are not per-tenant. A
 * workspace that has answered nothing reports zeroes, which is the honest
 * state for one that was just created.
 */
export default function DashboardPage() {
  const { session } = useAuth();
  const overview = useAsync(useCallback(() => api.analyticsOverview(30), []));
  const data = overview.data;
  const firstName = session?.full_name?.trim().split(" ")[0];

  return (
    <PageBody>
      <PageHeader
        title={firstName ? `Welcome back, ${firstName}` : "Welcome back"}
        description={`What ${session?.workspace_name ?? "your workspace"} has done with Anchor over the last 30 days.`}
        actions={
          <Link href="/assistant" className={buttonClass("primary", "md")}>
            <Bot className="size-4" />
            Ask Anchor
          </Link>
        }
      />

      {overview.error ? (
        <div className="mt-6">
          <ErrorNotice error={overview.error} onRetry={overview.reload} />
        </div>
      ) : null}

      <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {overview.loading || !data ? (
          <>
            <StatTileSkeleton />
            <StatTileSkeleton />
            <StatTileSkeleton />
            <StatTileSkeleton />
          </>
        ) : (
          <>
            <StatTile
              icon={<Activity className="size-4" />}
              label="Questions"
              value={formatNumber(data.requests.total)}
              hint={`${formatNumber(data.requests.errors)} errored · ${Math.round(data.requests.success_rate * 100)}% succeeded`}
            />
            <StatTile
              icon={<BookOpen className="size-4" />}
              label="Documents"
              value={formatNumber(data.documents.total)}
              hint={
                data.documents.failed
                  ? `${formatNumber(data.documents.chunks)} chunks · ${data.documents.failed} failed`
                  : `${formatNumber(data.documents.chunks)} indexed chunks`
              }
            />
            <StatTile
              icon={<Clock className="size-4" />}
              label="Latency"
              value={`${formatNumber(Math.round(data.latency_ms.average))} ms`}
              hint={`${formatNumber(Math.round(data.latency_ms.max))} ms slowest`}
            />
            <StatTile
              icon={<Wallet className="size-4" />}
              label="Spend"
              value={formatCost(data.cost.total_usd)}
              accent={data.cost.total_usd > 0}
              hint={`${formatNumber(data.tokens.total)} tokens · ${formatCost(data.cost.average_usd_per_request)}/question`}
            />
          </>
        )}
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Models used</CardTitle>
          </CardHeader>
          <CardBody>
            <BarList
              data={data?.by_model ?? {}}
              emptyTitle="No questions yet"
              emptyDescription="Once your workspace starts asking questions, the models Anchor routed them to are ranked here."
            />
          </CardBody>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Tool calls</CardTitle>
          </CardHeader>
          <CardBody>
            <BarList
              data={data?.tool_calls.by_name ?? {}}
              emptyTitle="No tool calls yet"
              emptyDescription="The calculator, knowledge-base search and ticket tools are ranked here once the model reaches for them."
            />
          </CardBody>
        </Card>
      </div>

      {data && data.requests.total === 0 ? (
        <Card className="mt-4">
          <CardBody>
            <EmptyState
              icon={<Bot className="size-6" />}
              title="Nothing recorded in this workspace yet"
              description="Upload a document to the knowledge base, then ask a question about it. Usage, latency and spend appear here as soon as Anchor has answered something."
              action={
                <Link href="/knowledge" className={buttonClass("primary", "sm")}>
                  <BookOpen className="size-3.5" />
                  Go to the knowledge base
                </Link>
              }
            />
          </CardBody>
        </Card>
      ) : null}

      <div className="mt-4 grid gap-4 sm:grid-cols-3">
        <QuickLink
          href="/assistant"
          icon={<Bot className="size-4" />}
          title="AI Assistant"
          description="Ask a question grounded in your knowledge base, with citations."
        />
        <QuickLink
          href="/knowledge"
          icon={<BookOpen className="size-4" />}
          title="Knowledge Base"
          description="Upload, re-index and remove the documents Anchor reads."
        />
        <QuickLink
          href="/team"
          icon={<Users className="size-4" />}
          title="Team & API keys"
          description="Invite members, set roles and issue scoped API keys."
        />
      </div>
    </PageBody>
  );
}

function QuickLink({
  href,
  icon,
  title,
  description,
}: {
  href: string;
  icon: React.ReactNode;
  title: string;
  description: string;
}) {
  return (
    <Link href={href} className="group">
      <Card className="h-full transition-colors group-hover:border-border-strong">
        <CardBody className="pt-4">
          <span className="flex items-center gap-2 text-fg">
            {icon}
            <span className="text-[13.5px] font-medium">{title}</span>
            <ArrowUpRight className="size-3.5 text-fg-subtle transition-colors group-hover:text-fg" />
          </span>
          <p className="mt-1.5 text-[12.5px] leading-relaxed text-fg-muted">{description}</p>
        </CardBody>
      </Card>
    </Link>
  );
}
