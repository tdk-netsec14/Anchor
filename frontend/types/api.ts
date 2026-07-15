/**
 * Response shapes for the Anchor FastAPI backend.
 *
 * Every type here mirrors a Pydantic model in `agent/schemas` or the literal
 * dict returned by a router — nothing is invented for the UI's benefit. When a
 * field is genuinely absent upstream it is typed as optional so the UI can
 * degrade honestly instead of rendering a confident blank.
 */

export type Role = "user" | "admin";

/* -- auth ---------------------------------------------------------------- */

export interface TokenResponse {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
  role: Role;
}

/** Principal as stored in the session cookie and read back by the UI. */
export interface Session {
  user_id: string;
  role: Role;
  expires_at: number;
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
}

export interface DocumentItem {
  doc_name: string;
  chunks: number;
  page_count: number;
  ocr_used: boolean;
}

export interface DocumentsResponse {
  documents: DocumentItem[];
  total_chunks: number;
}

/* -- observability ------------------------------------------------------- */

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
