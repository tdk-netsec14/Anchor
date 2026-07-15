"use client";

import { AnswerMeta, GuardrailNotice } from "@/components/assistant/AnswerMeta";
import { MarkdownAnswer } from "@/components/assistant/MarkdownAnswer";
import { PlainCitation, SourceCard } from "@/components/assistant/SourceCard";
import { ToolCallList } from "@/components/assistant/ToolCallList";
import { Spinner } from "@/components/ui/feedback";
import { ApiError, QueryResponse } from "@/types/api";

export function QuestionTurn({ question }: { question: string }) {
  return (
    <div className="flex justify-end animate-fade-up">
      <div className="max-w-[85%] rounded-2xl rounded-br-md bg-accent px-3.5 py-2.5 text-[14px] leading-relaxed text-accent-fg sm:max-w-[75%]">
        {question}
      </div>
    </div>
  );
}

export function AnswerTurn({
  response,
  error,
  pending,
}: {
  response: QueryResponse | null;
  error: ApiError | null;
  pending: boolean;
}) {
  if (pending) return <PendingAnswer />;
  if (error) return <FailedAnswer error={error} />;
  if (!response) return null;

  const detailed = new Map(response.source_details.map((d) => [d.citation, d]));

  return (
    <div className="animate-fade-up space-y-1">
      <div className="rounded-2xl rounded-bl-md border border-border bg-surface px-4 py-3.5">
        <MarkdownAnswer>{response.answer}</MarkdownAnswer>

        <ToolCallList calls={response.tool_calls} />
        <GuardrailNotice flags={response.guardrail_flags} />
        <AnswerMeta response={response} />
      </div>

      {response.sources.length > 0 ? (
        <details className="group rounded-xl border border-border bg-surface-2/40">
          <summary className="flex cursor-pointer list-none items-center gap-2 px-3.5 py-2.5 text-[13px] font-medium text-fg-muted transition-colors hover:text-fg">
            <span>
              {response.sources.length} source
              {response.sources.length === 1 ? "" : "s"}
            </span>
            <span className="text-[11.5px] font-normal text-fg-subtle">
              retrieved for this answer
            </span>
          </summary>
          <div className="space-y-1.5 border-t border-border p-2.5">
            {response.sources.map((citation, index) => {
              const detail = detailed.get(citation);
              return (
                <div key={`${citation}-${index}`}>
                  {detail ? (
                    <SourceCard detail={detail} />
                  ) : (
                    <PlainCitation citation={citation} />
                  )}
                </div>
              );
            })}
          </div>
        </details>
      ) : null}
    </div>
  );
}

function PendingAnswer() {
  return (
    <div className="animate-fade-up">
      <div className="flex items-center gap-2.5 rounded-2xl rounded-bl-md border border-border bg-surface px-4 py-3.5 text-[13px] text-fg-muted">
        <Spinner />
        Retrieving context and routing to a model…
      </div>
    </div>
  );
}

function FailedAnswer({ error }: { error: ApiError }) {
  return (
    <div className="animate-fade-up rounded-2xl rounded-bl-md border border-danger/30 bg-danger-soft/50 px-4 py-3">
      <p className="text-[13px] font-medium text-fg">{error.error}</p>
      <p className="mt-0.5 text-[13px] leading-relaxed text-fg-muted">{error.message}</p>
      {error.guardrail_flags.length > 0 ? (
        <p className="mt-1.5 font-mono text-[11px] text-fg-subtle">
          {error.guardrail_flags.join(", ")}
        </p>
      ) : null}
    </div>
  );
}
