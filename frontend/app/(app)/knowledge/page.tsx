"use client";

import { BookOpen, FileCheck2, Layers } from "lucide-react";
import { useCallback, useState } from "react";

import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { DocumentTable } from "@/components/knowledge/DocumentTable";
import { UploadPanel } from "@/components/knowledge/UploadPanel";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorNotice, Skeleton } from "@/components/ui/feedback";
import { useAuth } from "@/hooks/useAuth";
import { useAsync } from "@/hooks/useAsync";
import { api, toApiError } from "@/lib/api-client";
import { formatNumber } from "@/lib/format";
import { hasWorkspaceRole } from "@/types/api";
import { ApiError, DocumentItem } from "@/types/api";

export default function KnowledgePage() {
  const { session } = useAuth();
  const isAdmin = hasWorkspaceRole(session?.workspace_role, "admin");
  const [uploading, setUploading] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  const docs = useAsync(useCallback(() => api.documents(), []));
  const config = useAsync(useCallback(() => api.settings(), []));

  const maxUploadMb = config.data?.max_upload_mb ?? 0;

  async function upload(file: File) {
    setUploading(true);
    setNotice(null);
    try {
      const result = await api.uploadDocument(file, file.name);
      // With a database configured the response returns as soon as the bytes
      // are stored; a worker does the rest. Saying "indexed" here would be a
      // claim the backend has not made yet.
      setNotice(
        result.status === "queued"
          ? {
              tone: "success",
              text: `${result.doc_name} uploaded and queued. It appears as indexed once a worker has embedded it.`,
            }
          : {
              tone: "success",
              text: `Indexed ${result.doc_name} — ${formatNumber(result.chunks_created)} chunks from ${result.pages_processed} page(s)${result.pages_using_ocr ? `, ${result.pages_using_ocr} via OCR` : ""}.`,
            },
      );
      docs.reload();
    } catch (err) {
      const error: ApiError = toApiError(err);
      setNotice({ tone: "error", text: error.message });
    } finally {
      setUploading(false);
    }
  }

  async function remove(doc: DocumentItem) {
    const key = doc.id ?? doc.doc_name;
    setPending(key);
    setNotice(null);
    try {
      await api.deleteDocument(key);
      setNotice({ tone: "success", text: `Removed ${doc.doc_name} and its chunks.` });
      docs.reload();
    } catch (err) {
      setNotice({ tone: "error", text: toApiError(err).message });
    } finally {
      setPending(null);
    }
  }

  async function reindex(doc: DocumentItem) {
    if (!doc.id) return;
    setPending(doc.id);
    setNotice(null);
    try {
      await api.reindexDocument(doc.id);
      setNotice({ tone: "success", text: `Re-queued ${doc.doc_name} for indexing.` });
      docs.reload();
    } catch (err) {
      setNotice({ tone: "error", text: toApiError(err).message });
    } finally {
      setPending(null);
    }
  }

  return (
    <PageBody>
      <PageHeader
        title="Knowledge Base"
        description="Documents Anchor has indexed. Chunks from these files are the only material the assistant is allowed to ground its answers in."
      />

      <div className="mt-6 grid gap-4 sm:grid-cols-3">
        <StatCard
          icon={<BookOpen className="size-4" />}
          label="Documents"
          value={docs.data ? formatNumber(docs.data.documents.length) : null}
        />
        <StatCard
          icon={<Layers className="size-4" />}
          label="Indexed chunks"
          value={docs.data ? formatNumber(docs.data.total_chunks) : null}
        />
        <StatCard
          icon={<FileCheck2 className="size-4" />}
          label="Retrieval depth"
          value={config.data ? String(config.data.retriever_top_k) : null}
          hint="passages considered per question"
        />
      </div>

      {isAdmin ? (
        <Card className="mt-6">
          <CardHeader>
            <CardTitle>Ingest a document</CardTitle>
          </CardHeader>
          <CardBody>
            <UploadPanel maxUploadMb={maxUploadMb} busy={uploading} onUpload={upload} />
          </CardBody>
        </Card>
      ) : (
        <Card className="mt-6">
          <CardBody>
            <p className="py-1 text-[13px] leading-relaxed text-fg-muted">
              Ingesting documents requires the{" "}
              <span className="font-medium text-fg">admin</span> role or higher. You are{" "}
              <span className="font-medium text-fg">{session?.workspace_role ?? "a viewer"}</span>
              , so you can read this knowledge base but not add to it. The backend enforces
              this on every request, not just in this interface.
            </p>
          </CardBody>
        </Card>
      )}

      {notice ? (
        <p
          role="status"
          className={`mt-4 rounded-lg border px-3 py-2 text-[13px] leading-relaxed ${
            notice.tone === "success"
              ? "border-success/30 bg-success-soft/60 text-fg-muted"
              : "border-danger/30 bg-danger-soft/60 text-fg-muted"
          }`}
        >
          {notice.text}
        </p>
      ) : null}

      <Card className="mt-6 overflow-hidden">
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>Indexed documents</CardTitle>
          <Button size="sm" variant="ghost" onClick={docs.reload} disabled={docs.loading}>
            Refresh
          </Button>
        </CardHeader>

        {docs.loading ? (
          <CardBody className="space-y-2">
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-9 w-2/3" />
          </CardBody>
        ) : docs.error ? (
          <CardBody>
            <ErrorNotice error={docs.error} onRetry={docs.reload} />
          </CardBody>
        ) : docs.data && docs.data.documents.length > 0 ? (
          <DocumentTable
            documents={docs.data.documents}
            isAdmin={isAdmin}
            pending={pending}
            onDelete={remove}
            onReindex={reindex}
          />
        ) : (
          <EmptyState
            icon={<BookOpen className="size-6" />}
            title="The knowledge base is empty"
            description={
              isAdmin
                ? "Upload a PDF above and its chunks will be embedded and stored, ready to ground the assistant's answers."
                : "No documents have been indexed yet. An administrator needs to upload a PDF before the assistant can answer questions."
            }
          />
        )}
      </Card>
    </PageBody>
  );
}

function StatCard({
  icon,
  label,
  value,
  hint,
}: {
  icon: React.ReactNode;
  label: string;
  value: string | null;
  hint?: string;
}) {
  return (
    <Card>
      <CardBody className="pt-4">
        <div className="flex items-center gap-2 text-fg-subtle">
          {icon}
          <span className="text-[11px] font-semibold uppercase tracking-wider">{label}</span>
        </div>
        {value === null ? (
          <Skeleton className="mt-2 h-7 w-16" />
        ) : (
          <p className="mt-1.5 text-2xl font-semibold tracking-tight">{value}</p>
        )}
        {hint ? <p className="mt-0.5 text-[11.5px] text-fg-subtle">{hint}</p> : null}
      </CardBody>
    </Card>
  );
}
