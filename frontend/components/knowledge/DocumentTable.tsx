"use client";

import { FileText, ScanText, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatNumber } from "@/lib/format";
import { DocumentItem } from "@/types/api";

export function DocumentTable({
  documents,
  isAdmin,
  pendingDelete,
  onDelete,
}: {
  documents: DocumentItem[];
  isAdmin: boolean;
  pendingDelete: string | null;
  onDelete: (docName: string) => void;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[560px] border-collapse text-left">
        <thead>
          <tr className="border-b border-border">
            <Th className="pl-4">Document</Th>
            <Th>Pages</Th>
            <Th>Chunks</Th>
            <Th>Status</Th>
            {isAdmin ? <Th className="pr-4 text-right">Actions</Th> : null}
          </tr>
        </thead>
        <tbody>
          {documents.map((doc) => (
            <tr key={doc.doc_name} className="border-b border-border last:border-0">
              <td className="py-3 pl-4 pr-4">
                <span className="flex items-center gap-2">
                  <FileText className="size-3.5 shrink-0 text-fg-subtle" />
                  <span className="truncate text-[13px] font-medium">{doc.doc_name}</span>
                </span>
              </td>
              <td className="py-3 pr-4 text-[13px] text-fg-muted">{formatNumber(doc.page_count)}</td>
              <td className="py-3 pr-4 text-[13px] text-fg-muted">{formatNumber(doc.chunks)}</td>
              <td className="py-3 pr-4">
                <Badge tone={doc.ocr_used ? "accent" : "success"}>
                  {doc.ocr_used ? (
                    <>
                      <ScanText className="size-3" /> OCR
                    </>
                  ) : (
                    "Indexed"
                  )}
                </Badge>
              </td>
              {isAdmin ? (
                <td className="py-3 pr-4 text-right">
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={pendingDelete === doc.doc_name}
                    onClick={() => onDelete(doc.doc_name)}
                    aria-label={`Delete ${doc.doc_name}`}
                  >
                    <Trash2 className="size-3.5" />
                    {pendingDelete === doc.doc_name ? "Deleting…" : "Delete"}
                  </Button>
                </td>
              ) : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
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
