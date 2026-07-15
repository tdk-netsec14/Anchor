"use client";

import { FileUp, Loader2, X } from "lucide-react";
import { useRef, useState, type DragEvent } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/format";
import { ApiError } from "@/types/api";

type Props = {
  maxUploadMb: number;
  busy: boolean;
  onUpload: (file: File) => void;
};

/**
 * Drag-and-drop plus a file picker. The size check here is a courtesy that
 * saves a round trip; the backend enforces the real limit regardless.
 */
export function UploadPanel({ maxUploadMb, busy, onUpload }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [problem, setProblem] = useState<ApiError | null>(null);

  function accept(file: File | undefined) {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      setProblem({
        error: "unsupported_file",
        message: "Anchor indexes PDF documents. Choose a .pdf file.",
        guardrail_flags: [],
        status: 0,
      });
      return;
    }
    if (maxUploadMb > 0 && file.size > maxUploadMb * 1024 * 1024) {
      setProblem({
        error: "file_too_large",
        message: `That file is larger than the ${maxUploadMb} MB limit.`,
        guardrail_flags: [],
        status: 0,
      });
      return;
    }
    setProblem(null);
    onUpload(file);
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setOver(false);
    if (busy) return;
    accept(event.dataTransfer.files?.[0]);
  }

  return (
    <div>
      <div
        onDragOver={(e) => {
          e.preventDefault();
          if (!busy) setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
        className={cn(
          "rounded-xl border border-dashed px-5 py-8 text-center transition-colors",
          over ? "border-accent bg-accent-soft/50" : "border-border bg-surface",
          busy && "opacity-60",
        )}
      >
        {busy ? (
          <>
            <Loader2 className="mx-auto size-5 animate-spin text-accent" />
            <p className="mt-3 text-[13.5px] font-medium">Indexing document…</p>
            <p className="mt-1 text-[12.5px] text-fg-muted">
              Extracting text, running OCR where needed, embedding and storing chunks.
            </p>
          </>
        ) : (
          <>
            <FileUp className="mx-auto size-5 text-fg-subtle" />
            <p className="mt-3 text-[13.5px] font-medium">Drop a PDF here</p>
            <p className="mt-1 text-[12.5px] text-fg-muted">
              {maxUploadMb > 0 ? `Up to ${maxUploadMb} MB.` : "PDF documents only."}{" "}
              Re-uploading a name replaces the previous version.
            </p>
            <Button className="mt-4" onClick={() => inputRef.current?.click()}>
              Choose a file
            </Button>
          </>
        )}
      </div>

      <input
        ref={inputRef}
        type="file"
        accept="application/pdf,.pdf"
        className="hidden"
        onChange={(e) => {
          accept(e.target.files?.[0]);
          e.target.value = "";
        }}
      />

      {problem ? (
        <div className="mt-3 flex items-start gap-2 rounded-lg border border-danger/30 bg-danger-soft/60 px-3 py-2">
          <X className="mt-0.5 size-3.5 shrink-0 text-danger" />
          <p className="text-[12.5px] leading-relaxed text-fg-muted">{problem.message}</p>
        </div>
      ) : null}
    </div>
  );
}
