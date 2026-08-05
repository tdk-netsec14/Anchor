"use client";

import { FileText, RefreshCw, ScanText, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatNumber } from "@/lib/format";
import { DocumentItem } from "@/types/api";

/** How a document's pipeline state is presented. */
function statusBadge(doc: DocumentItem) {
  switch (doc.status) {
    case "indexed":
      return (
        <Badge tone="success">
          {doc.ocr_used ? (
            <>
              <ScanText className="size-3" /> OCR
            </>
          ) : (
            "Indexed"
          )}
        </Badge>
      );
    case "queued":
      return <Badge tone="warning">Queued</Badge>;
    case "processing":
      return <Badge tone="accent">Processing</Badge>;
    case "failed":
      return <Badge tone="danger">Failed</Badge>;
    default:
      return <Badge>{doc.status}</Badge>;
  }
}

export function DocumentTable({
  documents,
  isAdmin,
  pending,
  onDelete,
  onReindex,
}: {
  documents: DocumentItem[];
  isAdmin: boolean;
  /** Document currently being mutated, so only that row shows a spinner. */
  pending: string | null;
  onDelete: (doc: DocumentItem) => void;
  onReindex: (doc: DocumentItem) => void;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] border-collapse text-left">
        <thead>
          <tr className="border-b border-border">
            <Th className="pl-4">Document</Th>
            <Th>Status</Th>
            <Th>Pages</Th>
            <Th>Chunks</Th>
            <Th>Size</Th>
            {isAdmin ? <Th className="pr-4 text-right">Actions</Th> : null}
          </tr>
        </thead>
        <tbody>
          {documents.map((doc) => {
            const key = doc.id ?? doc.doc_name;
            const busy = pending === key;
            return (
              <tr key={key} className="border-b border-border last:border-0">
                <td className="py-3 pl-4 pr-4">
                  <span className="flex items-center gap-2">
                    <FileText className="size-3.5 shrink-0 text-fg-subtle" />
                    <span className="min-w-0">
                      <span className="block truncate text-[13px] font-medium">{doc.doc_name}</span>
                      {doc.uploaded_by ? (
                        <span className="block truncate text-[11px] text-fg-subtle">
                          {doc.uploaded_by}
                        </span>
                      ) : null}
                    </span>
                  </span>
                </td>
                <td className="py-3 pr-4">{statusBadge(doc)}</td>
                <td className="py-3 pr-4 text-[13px] text-fg-muted">{formatNumber(doc.page_count)}</td>
                <td className="py-3 pr-4 text-[13px] text-fg-muted">{formatNumber(doc.chunks)}</td>
                <td className="py-3 pr-4 text-[13px] text-fg-muted">
                  {formatBytes(doc.size_bytes)}
                </td>
                {isAdmin ? (
                  <td className="py-3 pr-4 text-right">
                    <span className="inline-flex items-center gap-1">
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={busy || !doc.id}
                        onClick={() => onReindex(doc)}
                        aria-label={`Re-index ${doc.doc_name}`}
                      >
                        <RefreshCw className="size-3.5" />
                        Re-index
                      </Button>
                      <Button
                        size="sm"
                        variant="danger"
                        disabled={busy}
                        onClick={() => onDelete(doc)}
                        aria-label={`Delete ${doc.doc_name}`}
                      >
                        <Trash2 className="size-3.5" />
                        Delete
                      </Button>
                    </span>
                  </td>
                ) : null}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "—";
  const mb = bytes / (1024 * 1024);
  if (mb < 1) return `${Math.round(bytes / 1024)} KB`;
  return `${mb.toFixed(1)} MB`;
}

function Th({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <th
      className={`py-2 text-[11px] font-semibold uppercase tracking-wider text-fg-subtle ${className}`}
    >
      {children}
    </th>
  );
}
