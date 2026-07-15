import { NextResponse } from "next/server";

import { BackendError, callBackend } from "@/lib/backend";
import { clearSessionCookie, getToken } from "@/lib/session";
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
    if (err instanceof BackendError) {
      // An expired or rejected token should not leave a dead cookie behind —
      // clearing it lets the client fall straight through to the login screen.
      if (err.status === 401) {
        await clearSessionCookie();
      }
      return NextResponse.json(err.toApiError(), { status: err.status });
    }
    throw err;
  }
}
