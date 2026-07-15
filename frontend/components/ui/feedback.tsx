import { AlertTriangle, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/format";
import { ApiError } from "@/types/api";
import { Button } from "./button";

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      role="status"
      aria-label="Loading"
      className={cn(
        "inline-block size-4 shrink-0 animate-spin rounded-full border-2",
        "border-current border-t-transparent opacity-70",
        className,
      )}
    />
  );
}

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      className={cn("animate-pulse rounded-md bg-surface-2", className)}
      aria-hidden="true"
    />
  );
}

/**
 * Shown when a request succeeded but there is nothing to report. Distinct from
 * `ErrorNotice` on purpose: "no data yet" is a normal state for a service
 * that has just started, and must not be dressed up as a failure.
 */
export function EmptyState({
  title,
  description,
  action,
  icon,
}: {
  title: string;
  description: string;
  action?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-14 text-center">
      {icon ? <div className="mb-1 text-fg-subtle">{icon}</div> : null}
      <p className="text-sm font-medium text-fg">{title}</p>
      <p className="max-w-sm text-[13px] leading-relaxed text-fg-muted">{description}</p>
      {action ? <div className="mt-3">{action}</div> : null}
    </div>
  );
}

export function ErrorNotice({
  error,
  onRetry,
  className,
}: {
  error: ApiError;
  onRetry?: () => void;
  className?: string;
}) {
  return (
    <div
      role="alert"
      className={cn(
        "rounded-xl border border-danger/30 bg-danger-soft/60 px-4 py-3",
        className,
      )}
    >
      <div className="flex items-start gap-2.5">
        <AlertTriangle className="mt-0.5 size-4 shrink-0 text-danger" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-fg">{headline(error)}</p>
          <p className="mt-0.5 text-[13px] leading-relaxed text-fg-muted">{error.message}</p>
          <p className="mt-1.5 font-mono text-[11px] text-fg-subtle">
            {error.error}
            {error.request_id ? ` · request ${error.request_id}` : ""}
          </p>
          {onRetry ? (
            <Button size="sm" variant="ghost" className="mt-2 -ml-1" onClick={onRetry}>
              <RefreshCw className="size-3.5" />
              Try again
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}

/** Give the common failure modes a sentence a person can act on. */
function headline(error: ApiError): string {
  switch (error.error) {
    case "network_error":
    case "backend_unreachable":
      return "Cannot reach the Anchor service";
    case "no_provider_available":
      return "No model provider is available";
    case "input_rejected":
      return "That question was blocked by an input guardrail";
    case "insufficient_role":
      return "This action requires the admin role";
    case "token_expired":
    case "invalid_token":
    case "unauthenticated":
      return "Your session has expired";
    case "upstream_timeout":
      return "The request timed out";
    default:
      return error.status === 0 ? "Something went wrong" : `Request failed (${error.status})`;
  }
}
