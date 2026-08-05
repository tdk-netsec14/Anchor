"use client";

import { MessageSquare, Pencil, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useState } from "react";

import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { MarkdownAnswer } from "@/components/assistant/MarkdownAnswer";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorNotice, Skeleton, Spinner } from "@/components/ui/feedback";
import { Input } from "@/components/ui/input";
import { useAsync } from "@/hooks/useAsync";
import { api, toApiError } from "@/lib/api-client";
import { cn, formatLatency, formatNumber } from "@/lib/format";

/**
 * Saved conversations.
 *
 * Answers are read back from the database with the metadata that was recorded
 * when they were produced — model, provider, latency, tokens, cost, sources
 * and tool calls — so a thread reopened next week still shows what actually
 * happened, rather than a bare transcript.
 */
export default function ConversationsPage() {
  const [selected, setSelected] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const list = useAsync(useCallback(() => api.conversations(), []));
  const detail = useAsync(
    useCallback(() => (selected ? api.conversation(selected) : Promise.resolve(null)), [selected]),
    [selected],
  );

  async function startNew() {
    setBusy(true);
    setError(null);
    try {
      const created = await api.createConversation();
      setSelected(created.id);
      list.reload();
    } catch (err) {
      setError(toApiError(err).message);
    } finally {
      setBusy(false);
    }
  }

  async function commitRename(id: string) {
    const title = draft.trim();
    if (!title) {
      setRenaming(null);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.renameConversation(id, title);
      setRenaming(null);
      list.reload();
    } catch (err) {
      setError(toApiError(err).message);
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    setBusy(true);
    setError(null);
    try {
      await api.deleteConversation(id);
      if (selected === id) setSelected(null);
      list.reload();
    } catch (err) {
      setError(toApiError(err).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <PageBody>
      <PageHeader
        title="Conversations"
        description="Every question your workspace has asked, kept with the model, cost and sources that produced the answer."
        actions={
          <Button variant="primary" onClick={startNew} disabled={busy}>
            {busy ? <Spinner /> : <Plus className="size-4" />}
            New conversation
          </Button>
        }
      />

      {error ? (
        <p
          role="alert"
          className="mt-4 rounded-lg border border-danger/30 bg-danger-soft/60 px-3 py-2 text-[13px] text-fg"
        >
          {error}
        </p>
      ) : null}

      <div className="mt-6 grid gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
        <Card className="h-fit overflow-hidden">
          <CardHeader>
            <CardTitle>Threads</CardTitle>
          </CardHeader>
          {list.loading ? (
            <CardBody className="space-y-2">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-2/3" />
            </CardBody>
          ) : list.error ? (
            <CardBody>
              <ErrorNotice error={list.error} onRetry={list.reload} />
            </CardBody>
          ) : list.data && list.data.length > 0 ? (
            <ul className="max-h-[32rem] overflow-y-auto">
              {list.data.map((conv) => {
                const active = conv.id === selected;
                const editing = conv.id === renaming;
                return (
                  <li
                    key={conv.id}
                    className={cn(
                      "border-t border-border",
                      active ? "bg-accent-soft/40" : "hover:bg-surface-2",
                    )}
                  >
                    {editing ? (
                      <div className="flex items-center gap-1.5 p-2">
                        <Input
                          value={draft}
                          onChange={(e) => setDraft(e.target.value)}
                          autoFocus
                          maxLength={200}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") void commitRename(conv.id);
                            if (e.key === "Escape") setRenaming(null);
                          }}
                        />
                        <Button size="sm" variant="ghost" onClick={() => void commitRename(conv.id)}>
                          Save
                        </Button>
                      </div>
                    ) : (
                      <div className="flex items-center gap-1 p-2">
                        <button
                          type="button"
                          onClick={() => setSelected(conv.id)}
                          className="min-w-0 flex-1 rounded-md px-2 py-1.5 text-left"
                        >
                          <span className="block truncate text-[13px] font-medium">{conv.title}</span>
                          <span className="block text-[11px] text-fg-subtle">
                            {conv.message_count} message{conv.message_count === 1 ? "" : "s"}
                          </span>
                        </button>
                        <Button
                          size="sm"
                          variant="ghost"
                          aria-label={`Rename ${conv.title}`}
                          onClick={() => {
                            setRenaming(conv.id);
                            setDraft(conv.title);
                          }}
                        >
                          <Pencil className="size-3.5" />
                        </Button>
                        <Button
                          size="sm"
                          variant="danger"
                          aria-label={`Delete ${conv.title}`}
                          disabled={busy}
                          onClick={() => void remove(conv.id)}
                        >
                          <Trash2 className="size-3.5" />
                        </Button>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          ) : (
            <EmptyState
              icon={<MessageSquare className="size-6" />}
              title="No conversations yet"
              description="Ask the assistant a question and the thread is saved here, along with the sources the answer was grounded in."
              action={
                <Link href="/assistant">
                  <Button variant="primary" size="sm">
                    Ask a question
                  </Button>
                </Link>
              }
            />
          )}
        </Card>

        <Card className="overflow-hidden">
          {!selected ? (
            <EmptyState
              icon={<MessageSquare className="size-6" />}
              title="Select a conversation"
              description="Pick a thread on the left to read it back with the model, latency, token usage and citations recorded at the time."
            />
          ) : detail.loading ? (
            <CardBody className="space-y-2">
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-24 w-full" />
              <Skeleton className="h-16 w-2/3" />
            </CardBody>
          ) : detail.error ? (
            <CardBody>
              <ErrorNotice error={detail.error} onRetry={detail.reload} />
            </CardBody>
          ) : detail.data ? (
            <>
              <CardHeader>
                <CardTitle>{detail.data.title}</CardTitle>
              </CardHeader>
              <CardBody className="space-y-5">
                {detail.data.messages.map((message) => (
                  <article key={message.id}>
                    <p className="text-[11px] font-semibold uppercase tracking-wider text-fg-subtle">
                      {message.role === "user" ? "You" : "Anchor"}
                    </p>
                    {message.role === "user" ? (
                      <p className="mt-1 whitespace-pre-wrap text-[13.5px] leading-relaxed text-fg">
                        {message.content}
                      </p>
                    ) : (
                      <>
                        <div className="mt-1">
                          <MarkdownAnswer>{message.content}</MarkdownAnswer>
                        </div>
                        <div className="mt-2 flex flex-wrap items-center gap-1.5">
                          {message.model_used ? <Badge>{message.model_used}</Badge> : null}
                          {message.provider ? <Badge>{message.provider}</Badge> : null}
                          <Badge>{formatLatency(message.latency_ms)}</Badge>
                          {message.prompt_tokens + message.completion_tokens > 0 ? (
                            <Badge>
                              {formatNumber(
                                message.prompt_tokens + message.completion_tokens,
                              )}{" "}
                              tokens
                            </Badge>
                          ) : null}
                          {message.cost_usd > 0 ? (
                            <Badge tone="warning">${message.cost_usd.toFixed(4)}</Badge>
                          ) : null}
                        </div>
                        {message.sources.length > 0 ? (
                          <ul className="mt-2 space-y-1">
                            {message.sources.map((source) => (
                              <li key={source.citation} className="text-[12px] text-fg-muted">
                                <span className="font-mono text-[11px] text-fg-subtle">
                                  {source.citation}
                                </span>{" "}
                                {source.doc_name}
                                {source.page_number ? ` · p.${source.page_number}` : ""}
                              </li>
                            ))}
                          </ul>
                        ) : null}
                        {message.tool_calls.length > 0 ? (
                          <ul className="mt-2 space-y-1">
                            {message.tool_calls.map((call, i) => (
                              <li key={`${call.name}-${i}`} className="text-[12px] text-fg-muted">
                                <Badge tone={call.ok ? "success" : "danger"}>{call.name}</Badge>{" "}
                                {call.result_preview}
                              </li>
                            ))}
                          </ul>
                        ) : null}
                      </>
                    )}
                  </article>
                ))}
              </CardBody>
            </>
          ) : null}
        </Card>
      </div>
    </PageBody>
  );
}
