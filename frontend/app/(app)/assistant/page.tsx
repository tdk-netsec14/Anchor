"use client";

import { Compass, RotateCcw } from "lucide-react";
import { useEffect, useRef } from "react";

import { Composer } from "@/components/assistant/Composer";
import { AnswerTurn, QuestionTurn } from "@/components/assistant/MessageTurn";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/hooks/useAuth";
import { useChat } from "@/hooks/useChat";
import { cn } from "@/lib/format";

const SUGGESTIONS = [
  "What does the leave policy say about carry-over?",
  "How do I report a lost company laptop?",
  "Summarise the incident response procedure.",
  "What is the expense reimbursement limit?",
];

export default function AssistantPage() {
  const { session } = useAuth();
  const { turns, busy, ask, stop, reset } = useChat();
  const endRef = useRef<HTMLDivElement>(null);

  // Follow the conversation as answers stream into the transcript.
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  const empty = turns.length === 0;

  return (
    <div className="flex h-[calc(100dvh-3.5rem)] flex-col">
      <div className="flex items-center justify-between border-b border-border px-5 py-3 sm:px-7">
        <div>
          <h1 className="text-sm font-semibold tracking-tight">Assistant</h1>
          <p className="text-[12px] text-fg-subtle">
            Answers are grounded in your indexed documents and cite their sources.
          </p>
        </div>
        {!empty ? (
          <Button size="sm" variant="ghost" onClick={reset}>
            <RotateCcw className="size-3.5" />
            New thread
          </Button>
        ) : null}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl px-5 py-6 sm:px-7">
          {empty ? (
            <div className="animate-fade-up flex min-h-[50vh] flex-col justify-center">
              <div className="mb-5 flex size-9 items-center justify-center rounded-xl border border-border bg-surface text-accent">
                <Compass className="size-4" />
              </div>
              <h2 className="text-[19px] font-semibold tracking-tight">
                What would you like to know{session ? `, ${session.user_id}` : ""}?
              </h2>
              <p className="mt-1.5 max-w-md text-[13.5px] leading-relaxed text-fg-muted">
                Anchor searches your private document set, routes the question to a model,
                and shows exactly which passages the answer came from.
              </p>

              <div className="mt-6 grid gap-2 sm:grid-cols-2">
                {SUGGESTIONS.map((text) => (
                  <button
                    key={text}
                    type="button"
                    onClick={() => ask(text)}
                    className={cn(
                      "rounded-lg border border-border bg-surface px-3.5 py-2.5 text-left",
                      "text-[13px] leading-relaxed text-fg-muted transition-colors",
                      "hover:border-accent/50 hover:text-fg",
                    )}
                  >
                    {text}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="space-y-5">
              {turns.map((turn) => (
                <div key={turn.id} className="space-y-2">
                  <QuestionTurn question={turn.question} />
                  <AnswerTurn
                    response={turn.response}
                    error={turn.error}
                    pending={turn.pending}
                  />
                </div>
              ))}
              <div ref={endRef} />
            </div>
          )}
        </div>
      </div>

      <div className="border-t border-border bg-bg px-5 py-3 sm:px-7">
        <div className="mx-auto w-full max-w-3xl">
          <Composer onSubmit={ask} onStop={stop} busy={busy} disabled={!session} />
        </div>
      </div>
    </div>
  );
}
