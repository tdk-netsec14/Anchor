"use client";

import { FileText, RefreshCw, TriangleAlert } from "lucide-react";
import { useCallback, useState } from "react";

import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button, buttonClass } from "@/components/ui/button";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorNotice, Skeleton, Spinner } from "@/components/ui/feedback";
import { useAuth } from "@/hooks/useAuth";
import { useAsync } from "@/hooks/useAsync";
import { api, toApiError } from "@/lib/api-client";
import { formatNumber } from "@/lib/format";
import { DocumentItem, hasWorkspaceRole } from "@/types/api";

/**
 * Ingestion operations.
 *
 * Knowledge Base is the content view — what Anchor can read. This page is the
 * pipeline view: which uploads have actually been embedded, which are still
 * queued behind a worker, and which failed and why. With a database
 * configured, an upload returns as soon as the bytes are stored, so a
 * document sitting in `queued` is the normal state for a moment, not a
 * promise that it is already searchable.
 */
export default function DocumentsPage() {
  const { session } = useAuth();
  const canAdmin = hasWorkspaceRole(session?.workspace_role, "admin");
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const docs = useAsync(useCallback(() => api.documents(), []));
  const all = docs.data?.documents ?? [];

  const byStatus = (status: string) => all.filter((d) => d.status === status);
  const failed = byStatus("failed");
  const inFlight = all.filter((d) => d.status === "queued" || d.status === "processing");
  const indexed = byStatus("indexed");

  async function reindex(doc: DocumentItem) {
    if (!doc.id) return;
    setPending(doc.id);
    setError(null);
    try {
      await api.reindexDocument(doc.id);
      docs.reload();
    } catch (err) {
      setError(toApiError(err).message);
    } finally {
      setPending(null);
    }
  }

  return (
    <PageBody>
      <PageHeader
        title="Documents"
        description="The state of every upload in this workspace, and what still has to be embedded before Anchor can quote it."
        actions={
          <Button size="sm" variant="ghost" onClick={docs.reload} disabled={docs.loading}>
            <RefreshCw className="size-3.5" />
            Refresh
          </Button>
        }
      />

      {error ? (
        <p role="alert" className="mt-4 rounded-lg border border-danger/30 bg-danger-soft/60 px-3 py-2 text-[13px] text-fg">
          {error}
        </p>
      ) : null}

      <div className="mt-6 grid gap-4 sm:grid-cols-3">
        <CountCard label="Indexed" value={indexed.length} tone="success" loading={docs.loading} />
        <CountCard label="In progress" value={inFlight.length} tone="warning" loading={docs.loading} />
        <CountCard label="Failed" value={failed.length} tone={failed.length ? "danger" : "neutral"} loading={docs.loading} />
      </div>

      <Card className="mt-6 overflow-hidden">
        <CardHeader>
          <CardTitle>Needs attention</CardTitle>
        </CardHeader>

        {docs.loading ? (
          <CardBody className="space-y-2">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-2/3" />
          </CardBody>
        ) : docs.error ? (
          <CardBody>
            <ErrorNotice error={docs.error} onRetry={docs.reload} />
          </CardBody>
        ) : failed.length === 0 && inFlight.length === 0 ? (
          <EmptyState
            icon={<FileText className="size-6" />}
            title="Every document is indexed"
            description="Nothing is queued and nothing has failed. Uploads appear here while a worker is still embedding them."
          />
        ) : (
          <CardBody className="space-y-2">
            {[...failed, ...inFlight].map((doc) => (
              <div
                key={doc.id ?? doc.doc_name}
                className="flex flex-wrap items-center gap-2 rounded-lg border border-border px-3 py-2.5"
              >
                {doc.status === "failed" ? (
                  <TriangleAlert className="size-4 shrink-0 text-danger" />
                ) : (
                  <Spinner className="size-4 text-fg-subtle" />
                )}
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px] font-medium">{doc.doc_name}</span>
                  <span className="block truncate text-[11.5px] text-fg-subtle">
                    {doc.status === "failed"
                      ? (doc.error_message ?? "The ingestion worker reported a failure.")
                      : "Uploaded and waiting for a worker to embed it."}
                  </span>
                </span>
                <Badge tone={doc.status === "failed" ? "danger" : "warning"}>{doc.status}</Badge>
                {canAdmin && doc.id ? (
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={pending === doc.id}
                    onClick={() => void reindex(doc)}
                  >
                    {pending === doc.id ? "Re-queueing…" : "Re-index"}
                  </Button>
                ) : null}
              </div>
            ))}
          </CardBody>
        )}
      </Card>

      <Card className="mt-4">
        <CardBody>
          <p className="text-[13px] leading-relaxed text-fg-muted">
            To add or remove documents, use the{" "}
            <a href="/knowledge" className={buttonClass("ghost", "sm", "inline-flex px-0")}>
              knowledge base
            </a>
            , where the upload panel and the full document list live.
          </p>
        </CardBody>
      </Card>
    </PageBody>
  );
}

function CountCard({
  label,
  value,
  tone,
  loading,
}: {
  label: string;
  value: number;
  tone: "success" | "warning" | "danger" | "neutral";
  loading: boolean;
}) {
  const colour = {
    success: "text-success",
    warning: "text-warning",
    danger: "text-danger",
    neutral: "text-fg",
  }[tone];

  return (
    <Card>
      <CardBody className="pt-4">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-fg-subtle">{label}</p>
        {loading ? (
          <Skeleton className="mt-2 h-7 w-12" />
        ) : (
          <p className={`mt-1.5 text-2xl font-semibold tracking-tight ${colour}`}>
            {formatNumber(value)}
          </p>
        )}
      </CardBody>
    </Card>
  );
}
