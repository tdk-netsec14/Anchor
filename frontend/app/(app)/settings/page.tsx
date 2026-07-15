"use client";

import { CheckCircle2, XCircle } from "lucide-react";
import { useCallback } from "react";

import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorNotice, Skeleton } from "@/components/ui/feedback";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api-client";
import { formatDuration, formatNumber } from "@/lib/format";

export default function SettingsPage() {
  const settings = useAsync(useCallback(() => api.settings(), []));
  const health = useAsync(useCallback(() => api.health(), []));

  return (
    <PageBody>
      <PageHeader
        title="Settings"
        description="How this Anchor instance is configured. Everything here is read from the backend at runtime — credentials, secrets and provider keys are never sent to the browser."
        actions={
          <Button
            size="sm"
            variant="secondary"
            onClick={() => {
              settings.reload();
              health.reload();
            }}
            disabled={settings.loading || health.loading}
          >
            Refresh
          </Button>
        }
      />

      {settings.loading ? (
        <div className="mt-6 space-y-4">
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      ) : settings.error ? (
        <ErrorNotice error={settings.error} onRetry={settings.reload} className="mt-6" />
      ) : settings.data ? (
        <div className="mt-6 space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Service</CardTitle>
            </CardHeader>
            <CardBody>
              <dl className="grid gap-x-8 gap-y-2.5 sm:grid-cols-2">
                <Row label="Application" value={settings.data.app_name} />
                <Row label="Version" value={settings.data.version} mono />
                <Row label="Environment" value={settings.data.environment} />
                <Row
                  label="Status"
                  value={
                    health.data
                      ? `${health.data.status} · up ${formatDuration(health.data.uptime_seconds)}`
                      : "checking…"
                  }
                />
              </dl>
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Model routing</CardTitle>
            </CardHeader>
            <CardBody className="space-y-4">
              <dl className="grid gap-x-8 gap-y-2.5 sm:grid-cols-2">
                <Row label="Default model" value={settings.data.default_model} mono />
                <Row
                  label="Fallback order"
                  value={
                    settings.data.fallback_chain.length
                      ? settings.data.fallback_chain.join(" → ")
                      : "none configured"
                  }
                  mono
                />
                <Row label="Temperature" value={String(settings.data.llm_temperature)} />
                <Row label="Max output tokens" value={formatNumber(settings.data.llm_max_tokens)} />
              </dl>

              <div>
                <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-fg-subtle">
                  Configured providers
                </p>
                {settings.data.configured_providers.length === 0 ? (
                  <p className="text-[13px] leading-relaxed text-fg-muted">
                    No provider is configured, so queries will fail with a
                    no-provider error until at least one API key or a local Ollama
                    instance is available.
                  </p>
                ) : (
                  <ul className="flex flex-wrap gap-1.5">
                    {settings.data.configured_providers.map((provider) => (
                      <li key={provider}>
                        <Badge tone="success">
                          <CheckCircle2 className="size-3" />
                          {provider}
                        </Badge>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Retrieval &amp; ingestion</CardTitle>
            </CardHeader>
            <CardBody>
              <dl className="grid gap-x-8 gap-y-2.5 sm:grid-cols-2 lg:grid-cols-3">
                <Row label="Embedding model" value={settings.data.embedding_model} mono />
                <Row label="Vector collection" value={settings.data.chroma_collection} mono />
                <Row label="Passages per query" value={String(settings.data.retriever_top_k)} />
                <Row label="Chunk size" value={`${settings.data.chunk_size_tokens} tokens`} />
                <Row label="Chunk overlap" value={`${settings.data.chunk_overlap_tokens} tokens`} />
                <Row label="Max question length" value={`${formatNumber(settings.data.query_max_chars)} chars`} />
                <Row
                  label="OCR"
                  value={settings.data.ocr_enabled ? `enabled (${settings.data.ocr_language})` : "disabled"}
                />
                <Row
                  label="Max upload"
                  value={settings.data.max_upload_mb > 0 ? `${settings.data.max_upload_mb} MB` : "unlimited"}
                />
              </dl>
            </CardBody>
          </Card>

          {health.data ? (
            <Card>
              <CardHeader>
                <CardTitle>Subsystem checks</CardTitle>
              </CardHeader>
              <CardBody>
                <ul className="grid gap-2 sm:grid-cols-2">
                  {Object.entries(health.data.checks).map(([name, value]) => (
                    <li key={name} className="flex items-center gap-2 text-[13px]">
                      {isOk(value) ? (
                        <CheckCircle2 className="size-3.5 shrink-0 text-success" />
                      ) : (
                        <XCircle className="size-3.5 shrink-0 text-danger" />
                      )}
                      <span className="text-fg-muted">{humanise(name)}</span>
                      <span className="ml-auto font-mono text-[11.5px] text-fg-subtle">
                        {describe(value)}
                      </span>
                    </li>
                  ))}
                </ul>
              </CardBody>
            </Card>
          ) : null}
        </div>
      ) : null}
    </PageBody>
  );
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-border/60 pb-2 last:border-0">
      <dt className="shrink-0 text-[13px] text-fg-muted">{label}</dt>
      <dd
        className={`min-w-0 truncate text-right text-[13px] ${mono ? "font-mono text-[12px]" : ""}`}
        title={value}
      >
        {value}
      </dd>
    </div>
  );
}

/** The health endpoint returns either a string status or a nested dict. */
function isOk(value: unknown): boolean {
  if (typeof value === "string") return value === "ok";
  if (value && typeof value === "object" && "status" in value) {
    return (value as { status: string }).status === "ok";
  }
  return false;
}

function describe(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object" && "status" in value) {
    const record = value as Record<string, unknown>;
    const extra = record.status === "ok" ? record.chunks ?? record.path : record.reason;
    return extra === undefined ? String(record.status) : `${record.status} · ${String(extra)}`;
  }
  return String(value);
}

function humanise(name: string): string {
  return name.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}
