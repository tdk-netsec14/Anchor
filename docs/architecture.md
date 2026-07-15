# Anchor — Architecture

This document explains how Anchor is put together and, where a decision was
not obvious, why it was made that way. It is the companion to the `README.md`,
which covers setup and operation.

---

## 1. System overview

Anchor answers internal support questions from a private document knowledge
base. Its purpose is ticket deflection: a support engineer should be reachable
by email only for the questions a document search genuinely cannot answer.

```
                  ┌──────────────────────────────────────────┐
  PDF / upload    │            ingestion pipeline           │
  ───────────────▶│  extract ─▶ (OCR) ─▶ chunk ─▶ embed    │
                  └───────────────────┬──────────────────────┘
                                      │ chunks + vectors
                                      ▼
                            ┌───────────────────────┐
                            │       ChromaDB        │  persisted
                            │  cosine, 384-dim      │  chroma_data/
                            └───────────┬───────────┘
                                        │ top-k search
   user query ──▶┌────────────────────────────────────────────┐
                 │              Agent API (FastAPI)            │
                 │                                             │
                 │  input guard ─▶ retrieve ─▶ route          │
                 │       │                     │              │
                 │       │            ┌────────┴────────┐     │
                 │       │            │  model router   │     │
                 │       │            └────────┬────────┘     │
                 │       │                     │              │
                 │       │        ┌────────────┴───────────┐  │
                 │       │        │  provider abstraction  │  │
                 │       │        │ ollama · groq · openai │  │
                 │       │        │ · gemini  (+ fallback) │  │
                 │       │        └────────────┬───────────┘  │
                 │       │                     │              │
                 │       │            ┌────────┴────────┐     │
                 │       │            │   tool loop     │     │
                 │       │            │ search_kb       │     │
                 │       │            │ calculator      │     │
                 │       │            │ create_ticket   │     │
                 │       │            └────────┬────────┘     │
                 │       ▼                     │              │
                 │  output guard ◀─────────────┘              │
                 │  PII · citations · schema                  │
                 └────────────────────┬────────────────────────┘
                                      ▼
                            JSON logs + /metrics

                  ┌──────────────────────────────────────────┐
                  │          evaluation harness             │
                  │  test_cases.json ─▶ API ─▶ 4 graders    │
                  │                    ─▶ results/<ts>.json  │
                  └──────────────────────────────────────────┘
```

### 1.1 The web application

A Next.js App Router application (`frontend/`) is the product surface. It is
not a second implementation of anything above — it is a client, and the Agent
API remains the only place authorisation and inference happen.

The one structural decision worth recording here is that **the browser never
calls the Agent API directly**. Authenticated traffic goes:

```
Browser ──▶ Next.js route handler (Vercel) ──▶ Agent API (Render)
            holds the httpOnly cookie,            verifies the JWT and
            attaches it as a Bearer token        enforces the role
```

Three things follow:

- The access token is never in `localStorage` and never in the client bundle.
  It is `httpOnly`, so injected script cannot read it, and it is applied to the
  upstream request by the server rather than the browser.
- The production path needs **no CORS grant**, because no browser origin
  reaches FastAPI. `CORS_ALLOWED_ORIGINS` can be empty in that topology.
- The proxy is not a trust boundary. It carries a credential; FastAPI re-verifies
  it and re-checks the role on every route, so a user-role token still gets 403
  on ingestion. Hiding a control in the UI is not what enforces that.

The cost of this shape is a timeout budget: a request has to fit inside the
frontend function's lifetime as well as the backend's own, so
`maxDuration` on the query route must exceed
`LLM_TIMEOUT_SECONDS × (1 + PROVIDER_MAX_RETRIES) × tool iterations`.

---

## 2. Services

Three independently runnable components share two infrastructure pieces:
configuration (`agent/config.py`) and the vector store
(`ingestion/vector_store.py`).

| Service | Entry point | Runs as | Purpose |
|---|---|---|---|
| **Ingestion** | `ingestion/main.py` | batch job | Walk `data/documents`, push every PDF through the pipeline, exit. |
| **Agent API** | `agent/main.py` | long-running service | Auth, guardrails, retrieval, routing, tool loop, metrics. |
| **Evaluation** | `eval/run_eval.py` | batch job | Drive the Agent API over HTTP, grade the answers, write a report. |

The Agent API also exposes `POST /ingest`, so interactive uploads do not require
running the batch worker. Both paths call the same
`ingestion.pipeline.ingest_document`, so they cannot drift apart.

### Why ingestion code lives in `ingestion/` but is used by the API

The API needs the embedder and the vector store to answer questions. Rather
than duplicate that code or bolt a service call onto the read path (which would
put availability and latency of two processes in series), the shared logic lives
in the `ingestion` package and both services import it. The consequence is
visible in the Dockerfiles: both images copy `agent/` **and** `ingestion/`, and
`PYTHONPATH=/app`.

---

## 3. Ingestion pipeline

```
bytes ─▶ extract_pages ─▶ chunk_pages ─▶ embed_documents ─▶ upsert_chunks
```

### Text extraction and OCR

`ingestion/ocr.py` uses pypdf for the text layer and falls back to Tesseract
when a page yields too little native text (< `OCR_MIN_CHARS_PER_PAGE`, default
40 characters). Scanned pages are rasterised with **pypdfium2** rather than
`pdf2image`: pypdfium2 ships a self-contained PDFium binary, whereas pdf2image
requires the poppler shared libraries in every runtime image, which is a poor
trade for one function call. Docker installs `tesseract-ocr`; on Windows set
`TESSERACT_CMD`, because the installer there does not add itself to `PATH`.

Both paths are exercised by the sample corpus:
`it_support_policy.pdf` (2 pages, text layer), `hr_leave_policy.pdf`,
`expense_reimbursement.pdf`, and `scanned_facilities_procedure.pdf` — which is
image-only by construction, so it can only be read through OCR.

### Chunking

`ingestion/chunker.py` splits **per page**, and only within a page, which costs
a little packing efficiency and buys exact citations: every chunk can name the
page it came from, which is what a support agent needs when escalating.

The single most important property is that **chunk text is sliced out of the
original page string, never reassembled from tokens.** An early implementation
joined token strings with spaces and silently corrupted the stored text
(`"3.2"` → `"3 . 2"`, `"VPN"` → `"vp ##n"`), which degraded both retrieval and
the context the model eventually read. The chunker now takes a `span_fn` that
returns the character span of every token (from the embedding model's offset
mapping) and slices the source text between those offsets, snapping to
whitespace so a word is never split.

Chunk ids are deterministic — `doc.pdf#p1#c0` — so re-ingesting a document
overwrites its chunks instead of duplicating them. A document that *shrank*
would leave stale trailing chunks, so a re-ingest also deletes the document's
existing rows first.

### Chunk size vs. the embedding model's limit

`CHUNK_SIZE_TOKENS` defaults to 500, but `all-MiniLM-L6-v2` only encodes **256**
tokens. A larger chunk would have its tail silently dropped at encode time, so
half of it would never be searchable. `build_chunks` therefore clamps the
configured size to the model's real maximum and logs
`ingestion.chunk_size_clamped` with both numbers. The setting is an upper
bound, not a promise.

### Embeddings

`all-MiniLM-L6-v2` runs in-process. It is ~80 MB, fast on CPU, and keeps the
entire retrieval path off the network — no embedding API key, no per-token
cost, and no document text leaving the host. For an internal support knowledge
base that is clearly the right trade. The model is loaded lazily and once per
process.

---

## 4. The Agent API request path

```
request
  │
  ├─▶ middleware: assign request_id, start timer        agent/main.py
  │
  ├─▶ JWT verify + role check                           agent/auth.py
  │
  ├─▶ input guard (empty / oversized / injection)       agent/guardrails/input_guard.py
  │
  ├─▶ embed query ─▶ top-k search                       agent/rag/retriever.py
  │
  ├─▶ route to a provider (explainable heuristics)      agent/routing/router.py
  │
  ├─▶ tool loop  ◀──▶  provider                         agent/agent.py
  │     ├─ validate tool name + arguments                agent/tools/registry.py
  │     ├─ execute, record, feed result back
  │     └─ repeat up to ROUTER_MAX_TOOL_ITERATIONS
  │
  ├─▶ output guard (PII, citations, structure)          agent/guardrails/output_guard.py
  │
  ├─▶ metrics + one structured log line                 agent/observability/
  │
  └─▶ QueryResponse
```

### Keeping the event loop free

Everything above the request/response boundary is synchronous CPU work: PDF
parsing, OCR, embedding, Chroma reads and writes, and tool execution. FastAPI
runs `async def` routes on a single event loop, so calling any of it directly
from a route would stall every other in-flight request for the duration — and
OCR alone is seconds.

The four blocking call sites therefore go to a worker thread via
`starlette.concurrency.run_in_threadpool`:

| Site | Blocking work |
|---|---|
| `POST /ingest` | the whole extraction → chunk → embed → store pipeline |
| `GET /health` | the vector-store disk read and the Tesseract version probe |
| `AnchorAgent.answer` | `Retriever.retrieve` (embed + query) |
| `AnchorAgent.answer` | `ToolRegistry.execute` (which re-enters the store) |

Measured on the running container: with a 59.7 s query in flight, `/health`
returned in **1.06 s** and a concurrent ingest in **1.5 s**.

### The agent loop

Retrieval is a prologue, not the whole story. The model is given the retrieved
context and the tool schemas, and may spend up to three round trips pulling
more (via `search_kb`), computing (via `calculator`) or escalating (via
`create_ticket`) before answering. Every tool call is validated, recorded and
fed back.

A failing tool returns an *error string* to the model rather than raising, so
the model can retry with corrected arguments. That is the whole point of
returning the reason instead of swallowing it — and it is why the registry
converts exceptions into `ToolResult(ok=False)` rather than letting them escape
into the loop.

If the iteration budget is exhausted the agent uses any prose the model
produced along the way, rather than discarding it.

### Tool calls written as text

Models in the 1B–3B range — which is what most people actually run through
Ollama — frequently imitate the tool schema by *writing* the JSON into their
message content instead of using the provider's tool-calling channel. Measured
against a local 3B model, roughly one question in ten did this. Three shapes
were observed and are all recovered by `agent/tools/parsing.py`:

```
{"type":"function","function":{"name":"calculator","arguments":{...}}}   # the canonical shape
create_ticket\n{"summary":"...","priority":"low"}                        # name on the line above
I'll use the search_kb function.\n{"name":"search_kb","parameters":{...}} # prose, then the call
```

The matching is deliberately strict, because a false positive would execute a
tool the model never meant to call: the content must contain exactly one
trailing JSON object, that object must carry a recognisable function-call shape,
and the name must already be a registered tool. Everything else is left alone
and returned to the user as an ordinary answer, and the recovered call then
goes through the same argument validation as a native one. A generation that
was cut off mid-JSON (`finish_reason == "length"`) is deliberately *not*
recovered — it is treated as malformed and retried instead.

### Prompt structure

Three messages: system prompt, a user turn holding the retrieved context, and
a second user turn holding the actual question. The retrieved context is
deliberately **not** concatenated into the system message, and the user query
is always its own message. That separation is a structural defence: a poisoned
document cannot masquerade as an instruction, and a user cannot "close out" the
system turn.

The system prompt lives in `agent/prompts.py` and is never returned by the API,
logged, or written into an evaluation artefact.

---

## 5. Provider abstraction

`agent/routing/providers/base.py` defines `LLMProvider`:

```python
class LLMProvider(ABC):
    name: str
    def is_configured(self) -> bool: ...
    async def generate(self, messages, *, tools=None,
                       temperature=None, max_tokens=None) -> LLMResponse: ...
    def cost_estimate(self, prompt_tokens, completion_tokens) -> float: ...
```

Everything above this line — the agent loop, the router, the tools, the
evaluation harness — is vendor-agnostic. `LLMResponse` normalises content, tool
calls, token counts and latency. Errors form a small taxonomy
(`ProviderTimeout`, `ProviderUnavailable`, `ProviderRateLimited`,
`ProviderAuthError`) so the router can decide what is worth retrying without
parsing strings.

| Provider | File | Notes |
|---|---|---|
| Ollama | `ollama_provider.py` | Native `/api/chat`. The only provider that works with zero credentials, so it is the default. |
| Groq | `groq_provider.py` | 12 lines: a configuration binding over the shared OpenAI-compatible class. |
| OpenAI | `openai_provider.py` | Hosts the shared `OpenAICompatibleProvider`. |
| Gemini | `gemini_provider.py` | Native API, with its own message/tool translation — the abstraction earning its keep. |

Most hosted vendors speak `/chat/completions`, so that wire format is
implemented once and subclassed; a fix to tool-call parsing lands everywhere at
once. Gemini does not, and is the one provider that needed real work.

Raw provider exceptions never reach the caller. `classify_http_error` maps
status codes onto the error taxonomy and deliberately drops the response body,
which routinely echoes request content and account details.

### Routing

Classification is deterministic and inspectable — length, tool-intent keywords,
analysis keywords. A support assistant whose model choice cannot be explained is
one nobody can tune. Every decision carries a `reason` that reaches the logs
and the response.

| Tier | Signal | Preference (cheapest first) |
|---|---|---|
| `simple` | short, no tool or analysis signal | ollama → groq → openai → gemini |
| `medium` | calculator / ticket / search keywords | groq → ollama → openai → gemini |
| `complex` | compare / analyse / troubleshoot, or very long | openai → gemini → groq → ollama |

`force_model` bypasses all of it and exists for testing and evaluation.

### Fallback

```
primary → (retry once if the error is retryable) → next configured provider → …
```

Retry is skipped for `ProviderAuthError` — bad credentials will not fix
themselves. Auth failures and non-2xx responses are logged by type, not by
message. If every provider fails the caller gets a clean 503; the attempt list
stays in the logs.

---

## 6. Security model

| Layer | What it does | What it does *not* do |
|---|---|---|
| JWT + RBAC | Every route except `GET /health` requires a valid token; `/ingest` requires `admin`. | Token issuance is demo-only — no password check, no user database. |
| Input guard | Rejects empty/oversized queries and known injection phrasings. | **Not a security boundary.** Bypassable by paraphrase, non-English text or encoding tricks. |
| System prompt | Never returned, logged, or stored. | — |
| Tool allow-list | Only the three registered tools can ever be called; arguments are schema-validated. | — |
| Calculator | AST-parsed against a whitelist. No `eval`, ever. | — |
| Output guard | Redacts PII, flags fabricated citations, retries malformed output once. | Regex-based; not a DLP product. |

**The important framing:** the input guard is telemetry, not a guarantee. The
defences that actually hold are structural — the system prompt is unreachable
by construction, the tool set is a fixed allow-list, tool arguments are
schema-validated, and the calculator parses rather than executes. An attack that
slips past the filter still cannot reach anything it did not already have.

The calculator deserves a note. The expression arrives as a string from a
language model, so it is parsed into an AST and walked against a whitelist of
numeric literals, six binary operators, two unary operators, and eleven named
math functions. Attribute access, subscripting, imports, lambdas, comprehensions
and name binding are rejected *by omission* — anything not explicitly permitted
raises. Exponent magnitude and result size are bounded so `9**9**9` fails fast
instead of burning CPU.

---

## 7. Observability

Every request gets a `request_id` (a UUID, or a caller-supplied `X-Request-ID`)
held in a `ContextVar`, so it is correct under both the threadpool and asyncio
without being threaded through every call. It appears in the logs, in the
response body, and in the `X-Request-ID` header.

Logs are one JSON object per line. A completed query logs `request_id`, user,
role, model, provider, routing reason, latency, tokens, estimated cost,
guardrail flags, tool calls, retrieval hits and any fallbacks. Provider
failures, tool calls, ingestion events and guardrail decisions are logged
separately.

Secrets are never logged: provider errors are recorded by type, tool exceptions
by type, and nothing logs a JWT, an API key or document text.

`GET /metrics` returns in-process counters — totals, error rate, per-model and
per-provider counts, latency aggregates, token and cost totals, tool-call counts
and guardrail-flag counts. It is per-process by design; see §9 for the upgrade
path.

**On cost:** `COST_INPUT_PER_MTOK` / `COST_OUTPUT_PER_MTOK` are **empty by
default**, and no prices are hardcoded. `estimated_cost_usd` is therefore
honestly `0.0` for local Ollama and for any provider whose rates you have not
configured, rather than a fabricated number. Populate the maps with your
contracted rates to get real figures.

---

## 8. Evaluation

`eval/test_cases.json` holds 20 fixed cases across four categories — factual,
tool_use, adversarial and out_of_scope. `eval/run_eval.py` calls the API once
per case and runs four independent graders:

| Grader | Question it answers | Depends on |
|---|---|---|
| `schema_grader` | Is the response a valid `QueryResponse`? | nothing |
| `exact_match_grader` | Do the expected key facts appear? | nothing |
| `semantic_similarity_grader` | Is the expected content conveyed, however phrased? | local embedding model |
| `llm_judge_grader` | Correctness / grounding / relevance, 1–5 | a judge model |

A case passes only when every applicable grader passes; tool-use cases
additionally require the named tool to have been called. Adversarial cases pass
by being *rejected* — the guardrail outcome is the result, not an error.

The semantic grader embeds the reference and each *sliding word-window* of the
answer of roughly the reference's size, and takes the maximum similarity. An
earlier version compared whole sentences instead, which failed 7 of 8 perfectly
correct answers: the reference "25 days" against a 13-word sentence is a near
miss on cosine similarity even though the fact is plainly there. The threshold
is not a guess — `scripts/calibrate_grader.py` re-grades a saved run and
reports the separation (correct answers 0.475–0.809, incorrect 0.226–0.432), and
0.45 sits in that gap.

`eval/run_eval.py --regrade-from <report.json>` re-runs the graders over a
saved run without paying for inference again, which is how those thresholds
were derived and how a grader change is evaluated.

**The harness was wrong before the system was.** The first full run scored 25%,
and inspecting the answers showed most of them were correct: the semantic
grader was mis-measuring, the judge was receiving an always-empty context, and
the refusal markers did not match the wording the model actually used. Fixing
those moved the same recorded answers to 75%. The remaining failures are real
and are listed in the README.

`eval/test_agent.py` runs the same cases under pytest so a regression fails CI.
Its grader unit tests always run and need no server, model or network — they
are what stops a grader from silently passing everything.

---

## 9. Known limitations and future work

**Intentional v1 simplifications**

- Demo JWT issuance (no passwords, no user database).
- Heuristic prompt-injection detection.
- In-memory, per-process metrics.
- Simulated ticket creation (a local JSON file, not a ticketing system).
- Local ChromaDB, single process, no horizontal scaling.

**Actual limitations of this build**

- Retrieval is a single dense-vector search with a fixed relevance floor. There
  is no reranking and no hybrid (keyword + vector) retrieval, so an exact
  identifier in a document ("TCK-4821") can retrieve poorly. This has been
  *observed*, not just feared: in one evaluation run, case `oos_003` (parental
  leave for secondary caregivers) retrieved nothing, and the model correctly
  refused a question it should have answered.
- Small local models are not reliable at argument faithfulness, but part of
  what looked like that was our own fault. Measured on `llama3.2:3b`, the
  calculator was asked for `(1250 * 0.15) + 40` on a question whose answer is
  187.5 — and that expression was character-for-character the few-shot example
  in the tool's own JSON schema. The model was copying our template. Removing
  the domain-shaped example and stating the "only numbers the user gave"
  constraint fixed the arithmetic cases. The separate case of a created ticket
  not being quoted back was genuinely the model's, and is now covered by the
  `reference_pattern` guarantee in `output_guard.surface_missing_references`,
  which appends the value and flags `tool_reference_surfaced` rather than
  silently patching the answer.
- The knowledge base is global; there is no per-user or per-team document
  visibility filter.
- The LLM judge is a local model and is noisy. On a correct answer it rated
  "the maximum hotel rate is 300 USD" as *not addressing the question* when
  configured at 1B. Treat its score as a signal, not a gate — the three
  deterministic graders are what should be trusted.
- The evaluation suite asserts on a fixed corpus with a small local model.
  Pass rates move with the model, so results are comparable run-to-run but not
  across model changes.
- Citation checking verifies that a named source *was retrieved*, not that the
  specific claim is supported by it.
- The API returns source citations, not the retrieved passages, so the eval
  judge assesses grounding against the reference key points plus the citations
  rather than the full context.
- `estimated_cost_usd` is 0.0 by design until cost rates are configured; no
  prices are hardcoded.

**Deliberate next steps** (not implemented, in rough priority order)

1. Hybrid retrieval + a cross-encoder reranker.
2. Claim-level citation verification instead of source-level.
3. A real prompt-injection classifier alongside the heuristics.
4. Prometheus/Grafana — reimplement `MetricsRegistry` as a client; nothing
   else changes.
5. OpenTelemetry tracing across retrieval → provider → tool.
6. Streaming responses with incremental citation display.
7. A persistent user store behind a real identity provider.
8. Per-document ACLs enforced at retrieval time.
9. Distributed vector storage and horizontal scaling.
