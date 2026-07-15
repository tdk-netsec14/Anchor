"use client";

import { useCallback, useRef, useState } from "react";

import { api, toApiError } from "@/lib/api-client";
import { ApiError, QueryResponse } from "@/types/api";

export type Turn = {
  id: string;
  question: string;
  response: QueryResponse | null;
  error: ApiError | null;
  pending: boolean;
};

let counter = 0;
const nextId = () => `turn_${++counter}`;

/**
 * Conversation state for the assistant.
 *
 * History is deliberately client-side only: the backend accepts a
 * `session_id` for correlation but does not store prior turns, and inventing a
 * server-side transcript would mean claiming a feature that does not exist.
 */
export function useChat() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const controller = useRef<AbortController | null>(null);

  const ask = useCallback(async (question: string) => {
    const id = nextId();
    setTurns((prev) => [
      ...prev,
      { id, question, response: null, error: null, pending: true },
    ]);
    setBusy(true);

    const active = new AbortController();
    controller.current = active;

    try {
      const response = await api.query({ query: question, session_id: id }, active.signal);
      setTurns((prev) =>
        prev.map((t) => (t.id === id ? { ...t, response, pending: false } : t)),
      );
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        // The user stopped it; drop the half-finished turn rather than
        // showing a failure they did not cause.
        setTurns((prev) => prev.filter((t) => t.id !== id));
        return;
      }
      setTurns((prev) =>
        prev.map((t) => (t.id === id ? { ...t, error: toApiError(err), pending: false } : t)),
      );
    } finally {
      // Only the request that still owns the controller may clear `busy`;
      // a superseded one must not mark a newer turn as finished.
      if (controller.current === active) {
        controller.current = null;
        setBusy(false);
      }
    }
  }, []);

  const stop = useCallback(() => {
    controller.current?.abort();
  }, []);

  const reset = useCallback(() => {
    controller.current?.abort();
    setTurns([]);
    setBusy(false);
  }, []);

  return { turns, busy, ask, stop, reset };
}
