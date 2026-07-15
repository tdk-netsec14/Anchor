import {
  ActivityResponse,
  ApiError,
  DocumentsResponse,
  HealthResponse,
  IngestResponse,
  MetricsResponse,
  QueryRequest,
  QueryResponse,
  Role,
  Session,
  SettingsResponse,
} from "@/types/api";

/**
 * The app's only network surface.
 *
 * Components never call `fetch`; they call a named method here. Everything
 * goes to this app's own `/api/*` routes, which attach the httpOnly session
 * cookie and proxy to FastAPI — so the token is never in the client bundle and
 * no component has to think about an `Authorization` header.
 */

/** Fired when the server rejects the session, so the shell can sign out. */
export const SESSION_EXPIRED = "anchor:session-expired";

function fail(error: ApiError, redirectOn401: boolean): never {
  if (error.status === 401 && redirectOn401) {
    window.dispatchEvent(new CustomEvent(SESSION_EXPIRED));
  }
  throw error;
}

/**
 * `redirectOn401` must be false when 401 is an expected answer rather than a
 * session dying — the initial session probe, chiefly. Dispatching the
 * expired-session event there bounced signed-out visitors off the public
 * landing page and straight to /login.
 */
async function request<T>(
  path: string,
  init: RequestInit = {},
  redirectOn401 = true,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      cache: "no-store",
      headers: init.body instanceof FormData ? init.headers : { "Content-Type": "application/json" },
    });
  } catch (err) {
    // A cancelled request is not a failure to report — the caller already
    // knows it stopped it, so let the abort propagate untouched.
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    return fail({
      error: "network_error",
      message:
        "Could not reach the Anchor service. Check that the backend is running and that ANCHOR_API_URL is correct.",
      guardrail_flags: [],
      status: 0,
    }, redirectOn401);
  }

  if (response.status === 204) return undefined as T;

  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }

  if (!response.ok) {
    const shaped = (body ?? {}) as Partial<ApiError>;
    return fail({
      error: shaped.error ?? "http_error",
      message: shaped.message ?? `Request failed with status ${response.status}.`,
      request_id: shaped.request_id ?? response.headers.get("X-Request-ID"),
      guardrail_flags: shaped.guardrail_flags ?? [],
      status: response.status,
    }, redirectOn401);
  }

  return body as T;
}

export const api = {
  /* -- session ---------------------------------------------------------- */

  async login(username: string, role: Role): Promise<Session> {
    return request<Session>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, role }),
    });
  },

  async logout(): Promise<void> {
    await request<{ status: string }>("/api/auth/logout", { method: "POST" });
  },

  /** Current principal, or null when signed out. Does not raise on 401. */
  async session(): Promise<Session | null> {
    try {
      return await request<Session>("/api/auth/session", {}, false);
    } catch (err) {
      if ((err as ApiError).status === 401) return null;
      throw err;
    }
  },

  /* -- product ---------------------------------------------------------- */

  query(body: QueryRequest, signal?: AbortSignal): Promise<QueryResponse> {
    return request<QueryResponse>("/api/backend/query", {
      method: "POST",
      body: JSON.stringify(body),
      signal,
    });
  },

  documents(): Promise<DocumentsResponse> {
    return request<DocumentsResponse>("/api/backend/documents");
  },

  deleteDocument(docName: string): Promise<{ status: string; doc_name: string }> {
    return request(`/api/backend/documents/${encodeURIComponent(docName)}`, {
      method: "DELETE",
    });
  },

  async uploadDocument(file: File, docName: string): Promise<IngestResponse> {
    const form = new FormData();
    form.append("file", file);
    form.append("doc_name", docName);
    return request<IngestResponse>("/api/backend/ingest", { method: "POST", body: form });
  },

  activity(): Promise<ActivityResponse> {
    return request<ActivityResponse>("/api/backend/activity");
  },

  metrics(): Promise<MetricsResponse> {
    return request<MetricsResponse>("/api/backend/metrics");
  },

  settings(): Promise<SettingsResponse> {
    return request<SettingsResponse>("/api/backend/settings");
  },

  /** Liveness plus provider/vector-store detail, for the Settings page. */
  health(): Promise<HealthResponse> {
    return request<HealthResponse>("/api/backend/health");
  },
};

/** Narrow an unknown thrown value to something renderable. */
export function toApiError(err: unknown): ApiError {
  if (err && typeof err === "object" && "error" in err && "status" in err) {
    return err as ApiError;
  }
  return {
    error: "unexpected_error",
    message: err instanceof Error ? err.message : "Something went wrong.",
    guardrail_flags: [],
    status: 0,
  };
}
