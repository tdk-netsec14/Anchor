"use client";

import { ArrowUp, Square } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";

import { Spinner } from "@/components/ui/feedback";
import { cn } from "@/lib/format";

export function Composer({
  onSubmit,
  onStop,
  busy,
  disabled,
}: {
  onSubmit: (question: string) => void;
  onStop: () => void;
  busy: boolean;
  disabled?: boolean;
}) {
  const [value, setValue] = useState("");
  const areaRef = useRef<HTMLTextAreaElement>(null);

  // Grow with content up to a ceiling, then scroll.
  useEffect(() => {
    const el = areaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [value]);

  function send() {
    const question = value.trim();
    if (!question || busy || disabled) return;
    onSubmit(question);
    setValue("");
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // Enter sends; Shift+Enter is a newline. Guard against IME composition,
    // where Enter commits a candidate rather than submitting.
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      send();
    }
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    send();
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="rounded-xl border border-border bg-surface focus-within:border-accent"
    >
      <textarea
        ref={areaRef}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={onKeyDown}
        rows={1}
        disabled={disabled}
        placeholder={disabled ? "Sign in to ask a question" : "Ask a question about your documents…"}
        aria-label="Your question"
        className="max-h-[200px] w-full resize-none bg-transparent px-3.5 pb-1 pt-3 text-[14px] leading-relaxed text-fg outline-none placeholder:text-fg-subtle"
      />
      <div className="flex items-center justify-between px-2.5 pb-2.5 pt-1">
        <span className="pl-1 text-[11px] text-fg-subtle">
          Enter to send · Shift+Enter for a new line
        </span>
        {busy ? (
          <button
            type="button"
            onClick={onStop}
            aria-label="Stop generating"
            className="inline-flex size-8 items-center justify-center rounded-lg border border-border text-fg-muted transition-colors hover:bg-surface-2 hover:text-fg"
          >
            <Square className="size-3 fill-current" />
          </button>
        ) : (
          <button
            type="submit"
            disabled={!value.trim() || disabled}
            aria-label="Send question"
            className={cn(
              "inline-flex size-8 items-center justify-center rounded-lg transition-colors",
              "disabled:pointer-events-none",
              value.trim() && !disabled
                ? "bg-accent text-accent-fg hover:bg-accent-hover"
                : "bg-surface-2 text-fg-subtle",
            )}
          >
            {disabled ? <Spinner className="size-3.5" /> : <ArrowUp className="size-4" />}
          </button>
        )}
      </div>
    </form>
  );
}
