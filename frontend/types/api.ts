/**
 * Response shapes for the Anchor FastAPI backend.
 *
 * Every type here mirrors a Pydantic model in `agent/schemas` or the literal
 * dict returned by a router — nothing is invented for the UI's benefit. When a
 * field is genuinely absent upstream it is typed as optional so the UI can
 * degrade honestly instead of rendering a confident blank.
 */

/** The legacy `role` claim every JWT carries. Not an authority on its own. */
export type Role = "user" | "admin";

/**
 * What a member may do inside one workspace, ordered most to least capable.
 * Mirrors `WorkspaceRole` in `agent/db/models.py`; the backend compares ranks,
 * so the UI only needs the order for enable/disable decisions.
 */
export const WORKSPACE_ROLES = ["owner", "admin", "member", "viewer"] as const;
export type WorkspaceRole = (typeof WORKSPACE_ROLES)[number];

const ROLE_RANK: Record<WorkspaceRole, number> = {
  owner: 40,
  admin: 30,
  member: 20,
  viewer: 10,
};

/** True when `role` is at least as capable as `minimum`. */
export function hasWorkspaceRole(
  role: WorkspaceRole | null | undefined,
  minimum: WorkspaceRole,
): boolean {
  if (!role) return false;
  return (ROLE_RANK[role] ?? 0) >= ROLE_RANK[minimum];
}

/* -- auth ---------------------------------------------------------------- */

/**
 * `POST /auth/login` and `/auth/register` — the real session.
 *
 * `access_token` and `refresh_token` never reach the browser: they are set as
 * httpOnly cookies by the Next.js route handler. Only the descriptive fields
 * below are used by the UI.
 */
export interface SessionResponse {
  access_token: string;
  refresh_token: string;
  token_type: "bearer";
  /** Access-token lifetime in seconds. */
  expires_in: number;
  user_id: string;
  email: string;
  full_name: string;
  workspace_id: string;
  workspace_name: string;
  workspace_role: WorkspaceRole;
}

/**
 * The principal as the client sees it.
 *
 * Held in a cookie of its own so the shell can render a name, a workspace and a
 * role without waiting on a round trip. It is a display cache, not a
 * credential: the token is verified by FastAPI on every request, and nothing
 * is authorised from this object alone.
 */
export interface Session {
  user_id: string;
  email: string;
  full_name: string;
  workspace_id: string;
  workspace_name: string;
  workspace_role: WorkspaceRole;
  /** Epoch milliseconds at which the access token stops being accepted. */
  expires_at: number;
}

export interface UserResponse {
  user_id: string;
  email: string;
  full_name: string;
  created_at: string;
  last_login_at: string | null;
}

export interface WorkspaceSummary {
  id: string;
  name: string;
  slug: string;
  role: WorkspaceRole;
  is_personal: boolean;
  created_at: string;
}


/* -- query --------------------------------------------------------------- */

export interface SourceDetail {
  citation: string;
  doc_name: string;
  page_number: number | null;
  chunk_id: string;
  score: number;
  excerpt: string;
}

export interface ToolCallRecord {
  name: string;
  ok: boolean;
  arguments: Record<string, unknown>;
  result_preview: string;
  latency_ms: number;
}

export interface QueryRequest {
  query: string;
  session_id?: string | null;
  force_model?: string | null;
}

export interface QueryResponse {
  answer: string;
  /** Flat citation strings; also the set the output guardrail verifies against. */
  sources: string[];
  source_details: SourceDetail[];
  model_used: string;
  tool_calls: ToolCallRecord[];
  latency_ms: number;
  tokens_used: number;
  estimated_cost_usd: number;
  guardrail_flags: string[];
  request_id: string;
  provider: string;
  routing_reason: string;
  prompt_tokens: number;
  completion_tokens: number;
  fallbacks: { provider: string; reason: string }[];
  session_id: string | null;
}

/* -- ingestion ----------------------------------------------------------- */

export interface IngestResponse {
  doc_name: string;
  chunks_created: number;
  status: string;
  pages_processed: number;
  pages_using_ocr: number;
  total_tokens: number;
  replaced_existing: boolean;
  warnings: string[];
  document_id?: string | null;
  document_status?: string;
}

/* -- documents ----------------------------------------------------------- */

export type DocumentStatus = "queued" | "processing" | "indexed" | "failed";

/**
 * One knowledge-base document.
 *
 * `status` is the ingest pipeline's own state, not a boolean: with a database
 * configured an upload returns as soon as the bytes are stored and a worker
 * moves the document through queued → processing → indexed (or failed) after
 * the response has already been sent. The UI has to show that honestly rather
 * than implying a finished index.
 */
export interface DocumentItem {
  id: string | null;
  doc_name: string;
  status: DocumentStatus | string;
  size_bytes: number;
  chunks: number;
  page_count: number;
  ocr_used: boolean;
  error_message: string | null;
  uploaded_by: string | null;
  created_at: string | null;
  indexed_at: string | null;
}

export interface DocumentsResponse {
  documents: DocumentItem[];
  total_chunks: number;
}

/* -- conversations ------------------------------------------------------- */

export interface ConversationSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface MessageRecord {
  id: string;
  role: string;
  content: string;
  created_at: string;
  model_used: string | null;
  provider: string | null;
  latency_ms: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
  request_id: string | null;
  sources: SourceDetail[];
  tool_calls: ToolCallRecord[];
  guardrail_flags: string[];
}

export interface ConversationDetail extends ConversationSummary {
  messages: MessageRecord[];
}

/* -- team ---------------------------------------------------------------- */

export interface MemberResponse {
  user_id: string;
  email: string;
  full_name: string;
  role: WorkspaceRole;
  is_active: boolean;
  joined_at: string;
  last_login_at: string | null;
}

export interface InviteResponse {
  id: string;
  email: string;
  role: WorkspaceRole;
  expires_at: string;
  /** The one and only time the full invite link is shown. */
  token: string;
}

/* -- api keys ------------------------------------------------------------ */

export interface ApiKeyResponse {
  id: string;
  name: string;
  key_prefix: string;
  scopes: string[];
  created_at: string;
  expires_at: string | null;
  revoked_at: string | null;
  last_used_at: string | null;
  created_by: string | null;
}

/** Returned once, at creation. The secret is never retrievable afterwards. */
export interface ApiKeyCreatedResponse extends ApiKeyResponse {
  key: string;
}

/* -- observability ------------------------------------------------------- */

/**
 * `GET /analytics/overview` — usage for the active workspace, computed from
 * rows the backend recorded. Every field is a real count; a deployment with no
 * recorded usage reports zeroes rather than a plausible-looking placeholder.
 */
export interface AnalyticsOverview {
  window_days: number;
  generated_at: string;
  requests: { total: number; errors: number; fallbacks: number; success_rate: number };
  latency_ms: { average: number; max: number };
  tokens: { prompt: number; completion: number; total: number };
  cost: { total_usd: number; average_usd_per_request: number };
  retrieval: { citations_returned: number };
  by_model: Record<string, number>;
  by_provider: Record<string, number>;
  by_user: Record<string, number>;
  tool_calls: { total: number; by_name: Record<string, number> };
  guardrail_flags: Record<string, number>;
  documents: { total: number; by_status: Record<string, number>; chunks: number; failed: number };
}

export interface TimeseriesPoint {
  date: string;
  requests: number;
  errors: number;
  tokens: number;
  cost_usd: number;
}

export interface TimeseriesResponse {
  window_days: number;
  points: TimeseriesPoint[];
}

/** One row of the security-relevant audit trail. */
export interface AuditEvent {
  id: string;
  created_at: string;
  action: string;
  actor_email: string;
  target_type: string | null;
  target_id: string | null;
  outcome: string;
  request_id: string | null;
  ip_address: string | null;
}

export interface AuditResponse {
  events: AuditEvent[];
}

export interface ActivityItem {
  id: string;
  timestamp: number;
  query: string;
  latency_ms: number;
  model: string;
  provider: string;
  tokens_used: number;
  cost_usd: number;
  tool_calls: string[];
  guardrail_flags: string[];
  sources_count: number;
  routing_reason: string;
}

export interface ActivityResponse {
  items: ActivityItem[];
}

export interface LatencyStats {
  average: number;
  max: number;
  total: number;
  p50?: number;
  p95?: number;
  p99?: number;
}

export interface MetricsResponse {
  uptime_seconds: number;
  total_requests: number;
  total_queries: number;
  total_errors: number;
  error_rate: number;
  total_guardrail_blocks: number;
  total_fallbacks: number;
  latency_ms: LatencyStats;
  tokens: { prompt: number; completion: number; total: number };
  cost: { total_usd: number; average_usd_per_query: number };
  requests_by_model: Record<string, number>;
  requests_by_provider: Record<string, number>;
  requests_by_endpoint: Record<string, number>;
  tool_calls: { total: number; by_name: Record<string, number> };
  guardrail_flags: Record<string, number>;
  routing_reasons: Record<string, number>;
  errors_by_type: Record<string, number>;
  ingestion: { documents: number; chunks: number };
}

/* -- service ------------------------------------------------------------- */

export interface HealthResponse {
  status: string;
  service: string;
  version: string;
  environment: string;
  uptime_seconds: number;
  checks: Record<string, unknown>;
}

/** `GET /settings` — safe configuration only; never credentials. */
export interface SettingsResponse {
  app_name: string;
  version: string;
  environment: string;
  default_model: string;
  fallback_chain: string[];
  configured_providers: string[];
  embedding_model: string;
  chroma_collection: string;
  retriever_top_k: number;
  chunk_size_tokens: number;
  chunk_overlap_tokens: number;
  query_max_chars: number;
  max_upload_mb: number;
  ocr_enabled: boolean;
  ocr_language: string;
  llm_temperature: number;
  llm_max_tokens: number;
}

/** The error body as it travels over HTTP — no transport status inside it. */
export interface ApiErrorBody {
  error: string;
  message: string;
  request_id?: string | null;
  guardrail_flags?: string[];
}

/** An error once the client has caught it and attached the HTTP status. */
export interface ApiError extends ApiErrorBody {
  guardrail_flags: string[];
  status: number;
}
