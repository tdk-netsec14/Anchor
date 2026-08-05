import { NextResponse } from "next/server";

import { BackendError, backendUrl, callBackend } from "@/lib/backend";
import { clearSession, getToken } from "@/lib/session";
import { ApiErrorBody } from "@/types/api";

/**
 * Authenticated reverse proxy to the Anchor backend.
 *
 * Every privileged read and write in the app goes through here. Two things
 * follow from that: the bearer token is attached server-side from the httpOnly
 * cookie, and the browser never needs a cross-origin request to FastAPI — so a
 * Vercel deployment talks to Render over plain HTTPS with no CORS grant on the
 * backend at all.
 *
 * Authorisation is *not* decided here. An `admin` action still fails with 403
 * if the role in the token says `user`, because FastAPI re-checks the role on
 * the route. This layer only carries the credential.
 */

type Params = { params: Promise<{ path: string[] }> };

/**
 * Vercel kills a serverless function at `maxDuration`. It has to exceed the
 * backend's own worst case, which is not one LLM call but
 * `LLM_TIMEOUT_SECONDS` × (1 + PROVIDER_MAX_RETRIES) × tool iterations.
 * Raise both together if you point this at a slower model.
 */
export const maxDuration = 300;

/**
 * Document names are arbitrary user input, so segments cannot be allow-listed
 * to word characters — "HR Leave Policy (2026).pdf" is an ordinary name, and
 * rejecting it would make those documents undeletable from the UI.
 *
 * What is rejected is anything that could change *which* endpoint is reached:
 * a path separator, a backslash, a control character, or a traversal segment.
 * The backend host is fixed, so this is a correctness guard, not a sandbox.
 */
function isSafeSegment(segment: string): boolean {
  if (!segment || segment === "." || segment === "..") return false;

  for (let i = 0; i < segment.length; i += 1) {
    const code = segment.charCodeAt(i);
    const isControl = code < 0x20 || code === 0x7f;
    const isSeparator = segment[i] === "/" || segment[i] === "\\";
    if (isControl || isSeparator) return false;
  }

  return true;
}

/**
 * Generous by default because a self-hosted model answering on CPU routinely
 * takes a minute, and the backend will happily keep trying provider fallbacks
 * for longer than that. A 30s ceiling turned valid answers into 504s.
 */
const DEFAULT_TIMEOUT_MS = 300_000;
const INGEST_TIMEOUT_MS = 600_000;

export async function GET(request: Request, ctx: Params) {
  return proxy(request, ctx, "GET");
}

export async function POST(request: Request, ctx: Params) {
  return proxy(request, ctx, "POST");
}

export async function DELETE(request: Request, ctx: Params) {
  return proxy(request, ctx, "DELETE");
}

async function proxy(request: Request, { params }: Params, method: string) {
  const token = await getToken();
  if (!token) {
    return NextResponse.json(
      { error: "unauthenticated", message: "Sign in to continue." } satisfies ApiErrorBody,
      { status: 401 },
    );
  }

  const { path } = await params;
  if (!path?.length || !path.every(isSafeSegment)) {
    return NextResponse.json(
      { error: "invalid_path", message: "Malformed backend path." } satisfies ApiErrorBody,
      { status: 400 },
    );
  }

  const incoming = new URL(request.url);
  const query = incoming.search;
  const target = `/${path.join("/")}${query}`;

  let body: BodyInit | null = null;
  let contentType: string | null = null;

  if (method !== "GET") {
    // Multipart uploads arrive as FormData; forwarding the parsed form
    // re-encodes it with a fresh boundary, which FastAPI accepts.
    const isForm = request.headers.get("content-type")?.includes("multipart/form-data");
    if (isForm) {
      body = await request.formData();
    } else {
      const text = await request.text();
      body = text.length ? text : null;
      contentType = request.headers.get("content-type") ?? "application/json";
    }
  }

  try {
    const result = await callBackend({
      path: target,
      method,
      token,
      body,
      contentType,
      // Ingestion embeds and OCRs a document; give it room beyond the default.
      timeoutMs: target.startsWith("/ingest") ? INGEST_TIMEOUT_MS : DEFAULT_TIMEOUT_MS,
    });

    return NextResponse.json(result.body, {
      status: result.status,
      headers: result.requestId ? { "X-Request-ID": result.requestId } : undefined,
    });
  } catch (err) {
    if (!(err instanceof BackendError)) throw err;

    // An expired access token is the ordinary case for a tab left open, not a
    // reason to sign the person out. Redeem the refresh token once and replay
    // the request, rather than making every interaction fail until they log in
    // again. A 401 the refresh cannot fix is a real rejection, and only then is
    // the dead cookie cleared.
    if (err.status === 401 && (await refreshSession())) {
      try {
        const retried = await callBackend({
          path: target,
          method,
          token: (await getToken()) ?? undefined,
          body,
          contentType,
          timeoutMs: target.startsWith("/ingest") ? INGEST_TIMEOUT_MS : DEFAULT_TIMEOUT_MS,
        });
        return NextResponse.json(retried.body, {
          status: retried.status,
          headers: retried.requestId ? { "X-Request-ID": retried.requestId } : undefined,
        });
      } catch (retryErr) {
        if (!(retryErr instanceof BackendError)) throw retryErr;
        return NextResponse.json(retryErr.toApiError(), { status: retryErr.status });
      }
    }

    if (err.status === 401) {
      // Clearing it lets the client fall straight through to the login screen.
      await clearSession();
    }
    return NextResponse.json(err.toApiError(), { status: err.status });
  }
}

/**
 * Redeem the refresh token for a new access token.
 *
 * The call is made by hand rather than through `callBackend` because the
 * failure has to stay silent: a refresh that does not work is not an error the
 * person browsing the app did anything about, and surfacing it as one would
 * turn a routine token expiry into a red banner.
 */
async function refreshSession(): Promise<boolean> {
  try {
    const response = await fetch(`${backendUrl()}/api/auth/refresh`, {
      method: "POST",
      cache: "no-store",
    });
    return response.ok;
  } catch {
    return false;
  }
}
