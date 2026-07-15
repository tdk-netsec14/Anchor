import "server-only";

import { ApiError } from "@/types/api";

/**
 * Server-side handle on the Anchor FastAPI service.
 *
 * The browser never talks to FastAPI directly. Every authenticated call is
 * proxied through a Next.js route handler that attaches the bearer token held
 * in an httpOnly cookie, which keeps the token out of `localStorage` and off
 * the client bundle, and means production needs no CORS preflight between
 * Vercel and Render.
 */

const DEFAULT_TIMEOUT_MS = 30_000;

export function backendUrl(): string {
  const url =
    process.env.ANCHOR_API_URL?.trim() ||
    process.env.NEXT_PUBLIC_API_URL?.trim() ||
    "http://127.0.0.1:8000";
  return url.replace(/\/+$/, "");
}

export class BackendError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;
  readonly guardrailFlags: string[];

  constructor(init: {
    status: number;
    code: string;
    message: string;
    requestId?: string | null;
    guardrailFlags?: string[];
  }) {
    super(init.message);
    this.name = "BackendError";
    this.status = init.status;
    this.code = init.code;
    this.requestId = init.requestId ?? null;
    this.guardrailFlags = init.guardrailFlags ?? [];
  }

  toApiError(): ApiError {
    return {
      error: this.code,
      message: this.message,
      request_id: this.requestId,
      guardrail_flags: this.guardrailFlags,
      status: this.status,
    };
  }
}

type BackendCall = {
  path: string;
  method: string;
  token?: string;
  body?: BodyInit | null;
  contentType?: string | null;
  timeoutMs?: number;
};

type BackendResult = {
  status: number;
  body: unknown;
  requestId: string | null;
};

/**
 * Perform one call against the backend and normalise every failure mode —
 * transport, timeout, non-2xx, unparseable body — into `BackendError` so
 * callers have exactly one thing to handle.
 */
export async function callBackend({
  path,
  method,
  token,
  body,
  contentType,
  timeoutMs = DEFAULT_TIMEOUT_MS,
}: BackendCall): Promise<BackendResult> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  let response: Response;
  try {
    response = await fetch(`${backendUrl()}${path}`, {
      method,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(contentType ? { "Content-Type": contentType } : {}),
      },
      body: body ?? null,
      signal: controller.signal,
      cache: "no-store",
    });
  } catch (err) {
    const aborted = err instanceof Error && err.name === "AbortError";
    throw new BackendError({
      status: aborted ? 504 : 502,
      code: aborted ? "upstream_timeout" : "backend_unreachable",
      message: aborted
        ? `The Anchor service did not respond within ${timeoutMs / 1000}s.`
        : `Could not reach the Anchor service at ${backendUrl()}.`,
    });
  } finally {
    clearTimeout(timer);
  }

  const requestId = response.headers.get("X-Request-ID");
  const text = await response.text();
  let parsed: unknown = null;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = null;
    }
  }

  if (!response.ok) {
    // FastAPI's own validation errors arrive as {detail: [...]}, while the
    // application raises {error, message}. Both become BackendError.
    const detail = (parsed as { detail?: unknown } | null)?.detail;
    const shaped = (detail ?? parsed) as Partial<ApiError> | null;
    throw new BackendError({
      status: response.status,
      code: shaped?.error ?? "http_error",
      message: shaped?.message ?? `Request failed with status ${response.status}.`,
      requestId: shaped?.request_id ?? requestId,
      guardrailFlags: shaped?.guardrail_flags ?? [],
    });
  }

  return { status: response.status, body: parsed, requestId };
}
