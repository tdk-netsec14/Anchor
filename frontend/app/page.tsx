import { GithubIcon } from "@/components/ui/GithubIcon";
import {
  Boxes,
  Database,
  Eye,
  GitBranch,

  Layers,
  Route,
  ScanText,
  ShieldCheck,
  Terminal,
  Wrench,
} from "lucide-react";
import Link from "next/link";

import { Mark } from "@/components/ui/Mark";
import { Nav } from "@/components/landing/Nav";
import { FeatureCard, FeatureGrid, Section } from "@/components/landing/Section";
import { buttonClass } from "@/components/ui/button";

const REPO = "https://github.com/tdk-netsec14/Anchor";

export default function LandingPage() {
  return (
    <div className="min-h-dvh">
      <Nav />

      {/* -- hero -------------------------------------------------------- */}
      <section className="relative overflow-hidden">
        {/* A single quiet wash behind the hero; the page does its work with
            type and spacing, not with effects. */}
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-x-0 top-0 h-[420px] bg-[radial-gradient(60%_100%_at_50%_0%,var(--accent-soft),transparent)] opacity-70"
        />
        <div className="relative mx-auto max-w-6xl px-5 pb-20 pt-16 sm:px-7 sm:pb-24 sm:pt-24">
          <div className="animate-fade-up max-w-3xl">
            <span className="inline-flex items-center gap-2 rounded-full border border-border bg-surface px-3 py-1 text-[12px] text-fg-muted">
              <span className="size-1.5 rounded-full bg-accent" />
              Private knowledge · grounded answers
            </span>

            <h1 className="mt-6 text-[40px] font-semibold leading-[1.05] tracking-[-0.03em] sm:text-[60px]">
              Your private knowledge,
              <br />
              <span className="text-accent">grounded answers.</span>
            </h1>

            <p className="mt-6 max-w-2xl text-[16px] leading-relaxed text-fg-muted sm:text-[17px]">
              Anchor answers questions from your own documents. It retrieves the relevant
              passages, routes the question to an appropriate model, lets the model call
              tools, and shows you exactly which pages the answer came from — with
              guardrails on both sides of the model call.
            </p>

            <div className="mt-8 flex flex-wrap items-center gap-3">
              <Link href="/login" className={buttonClass("primary", "lg")}>
                Open Anchor
              </Link>
              <a
                href={REPO}
                target="_blank"
                rel="noreferrer"
                className={buttonClass("secondary", "lg")}
              >
                <GithubIcon className="size-4" />
                View on GitHub
              </a>
            </div>

            <p className="mt-6 font-mono text-[12px] text-fg-subtle">
              FastAPI · Next.js · ChromaDB · JWT + RBAC · Docker
            </p>
          </div>
        </div>
      </section>

      {/* -- what it does ------------------------------------------------- */}
      <Section
        id="capabilities"
        eyebrow="What Anchor does"
        title="An internal support and research agent, not a chat wrapper."
        lede="Every capability below is implemented in the backend and exercised by the test suite. Anchor is designed to answer from a bounded set of documents and to be accountable for what it says."
      >
        <FeatureGrid>
          <FeatureCard icon={<Layers className="size-4" />} title="Grounded retrieval">
            Questions are embedded and matched against an indexed document set. The model
            is given only the retrieved passages and is instructed to cite them.
          </FeatureCard>
          <FeatureCard icon={<Route className="size-4" />} title="Tiered model routing">
            Each question is classified and routed to an appropriate model, with an
            automatic fallback chain when a provider cannot serve it.
          </FeatureCard>
          <FeatureCard icon={<Wrench className="size-4" />} title="Tool calling">
            The model can call a sandboxed calculator, search the knowledge base, and
            create a support ticket, in a bounded tool loop.
          </FeatureCard>
          <FeatureCard icon={<ShieldCheck className="size-4" />} title="Input and output guardrails">
            Inputs are screened before any expensive work. Outputs are checked for PII and
            for citations that name a source that was never retrieved.
          </FeatureCard>
          <FeatureCard icon={<Eye className="size-4" />} title="Observability">
            Structured JSON logs with request correlation, plus a metrics registry behind
            the Analytics screen.
          </FeatureCard>
          <FeatureCard icon={<ScanText className="size-4" />} title="Document ingestion">
            PDFs are parsed, chunked, embedded and stored, with OCR for scanned pages
            before the text is indexed.
          </FeatureCard>
        </FeatureGrid>
      </Section>

      {/* -- rag ---------------------------------------------------------- */}
      <Section
        id="rag"
        eyebrow="Retrieval"
        title="Citations the guardrail can actually check."
        lede="Each retrieved chunk is emitted with a stable tag, its document, its page and a relevance score. The model is told to cite those tags — which turns “did it make that up?” into a mechanical check rather than a judgement call."
      >
        <div className="grid gap-6 lg:grid-cols-[1.1fr_1fr] lg:items-center">
          <div className="space-y-3">
            <PipelineStep
              n="1"
              title="Parse and chunk"
              body="PDF text is extracted page by page, OCR'd where a page has no text layer, then split into overlapping token windows."
            />
            <PipelineStep
              n="2"
              title="Embed and store"
              body="Chunks are embedded and written to a Chroma collection, with the document name, page number and chunk id kept as metadata."
            />
            <PipelineStep
              n="3"
              title="Retrieve and cite"
              body="The question is embedded, over-fetched and trimmed. Low-scoring noise is dropped so the model is not tempted by irrelevant context."
            />
            <PipelineStep
              n="4"
              title="Verify"
              body="The output guardrail rejects a citation that does not correspond to a retrieved source, and the answer is redacted of PII."
            />
          </div>
          <ContextBlock />
        </div>
      </Section>

      {/* -- routing ------------------------------------------------------ */}
      <Section
        id="routing"
        eyebrow="Model routing"
        title="More than one provider, and a plan when one is down."
        lede="Anchor talks to Ollama, Groq, OpenAI and Gemini behind one interface. Routing is tiered so cheap, routine questions do not pay for the largest model, and failures fall through to the next configured provider rather than returning a broken answer."
      >
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {[
            ["Ollama", "Local, offline, no API cost. The default in development."],
            ["Groq", "Low-latency hosted inference."],
            ["OpenAI", "Hosted GPT models."],
            ["Gemini", "Hosted Google models."],
          ].map(([name, note]) => (
            <div key={name} className="rounded-xl border border-border bg-surface p-4">
              <p className="font-mono text-[13px] font-medium">{name}</p>
              <p className="mt-1.5 text-[12.5px] leading-relaxed text-fg-muted">{note}</p>
            </div>
          ))}
        </div>
        <p className="mt-4 text-[13px] leading-relaxed text-fg-muted">
          Which providers are actually configured is shown in Settings at runtime —
          Anchor does not claim a provider it cannot reach.
        </p>
      </Section>

      {/* -- tools -------------------------------------------------------- */}
      <Section
        id="tools"
        eyebrow="Tools"
        title="The model can act, within bounds."
        lede="Tool calls are parsed from the model's output, validated against a registry schema, and executed server-side. A tool failure is fed back to the model rather than swallowed, so it can correct itself — and the loop is capped."
      >
        <div className="grid gap-3 sm:grid-cols-3">
          <FeatureCard icon={<Terminal className="size-4" />} title="calculator">
            Evaluates arithmetic as a sandboxed Python AST — never eval, so an expression
            cannot reach the filesystem or the network.
          </FeatureCard>
          <FeatureCard icon={<Database className="size-4" />} title="search_kb">
            Runs a second retrieval over the same knowledge base the assistant uses, and
            adds the passages it finds to the answer&rsquo;s sources.
          </FeatureCard>
          <FeatureCard icon={<Boxes className="size-4" />} title="create_ticket">
            Raises a support ticket and hands the conversation to a human when the answer
            is beyond what the documents can settle.
          </FeatureCard>
        </div>
      </Section>

      {/* -- guardrails --------------------------------------------------- */}
      <Section
        id="guardrails"
        eyebrow="Guardrails"
        title="The narrow waist."
        lede="A prompt-injection payload in an ingested document must not become an instruction, and a hallucinated citation must not reach the user. Both are handled structurally, not by asking the model nicely."
      >
        <FeatureGrid>
          <FeatureCard icon={<ShieldCheck className="size-4" />} title="Input screening">
            Over-long and prompt-injection shaped queries are rejected before retrieval
            or any provider call, and the refusal is returned as a typed error.
          </FeatureCard>
          <FeatureCard icon={<ShieldCheck className="size-4" />} title="Citation verification">
            The answer is checked against the sources actually retrieved. A citation to
            something never returned is flagged rather than silently trusted.
          </FeatureCard>
          <FeatureCard icon={<ShieldCheck className="size-4" />} title="PII redaction">
            Emails, phone numbers, card-shaped digit runs and similar patterns are
            redacted from output before the response is returned.
          </FeatureCard>
        </FeatureGrid>
      </Section>

      {/* -- observability ------------------------------------------------ */}
      <Section
        id="observability"
        eyebrow="Observability"
        title="Every request carries an id from end to end."
        lede="One request id is minted at the edge, attached to the access log, echoed back to the caller, and carried on the error envelope so a user-visible failure can be found in the logs."
      >
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <FeatureCard icon={<Eye className="size-4" />} title="Structured logging">
            JSON logs with request id, user, model, provider, routing reason, latency,
            tokens, cost and guardrail flags on every completed query.
          </FeatureCard>
          <FeatureCard icon={<Layers className="size-4" />} title="Metrics registry">
            In-process counters for requests, latency, tokens, cost, tool calls, guardrail
            events and fallbacks, served from a single snapshot.
          </FeatureCard>
          <FeatureCard icon={<GitBranch className="size-4" />} title="Activity view">
            The last 50 queries with their model, provider, cost and guardrail outcomes,
            so a regression is visible without reading logs.
          </FeatureCard>
          <FeatureCard icon={<ShieldCheck className="size-4" />} title="Auth and RBAC">
            JWT verification on every protected route, with a separate admin role required
            to write to the knowledge base.
          </FeatureCard>
        </div>
      </Section>

      {/* -- evaluation --------------------------------------------------- */}
      <Section
        id="evaluation"
        eyebrow="Evaluation"
        title="Answer quality is a regression test, not an opinion."
        lede="A fixed set of question-and-answer cases runs against the live API, and four independent graders score each response. The same cases also run under pytest, so a drop in answer quality fails the suite."
      >
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {[
            ["Exact match", "Checks a required substring or fact appears in the answer."],
            ["Schema", "Validates the response shape against the API contract."],
            ["LLM judge", "Scores relevance and faithfulness with a model grader."],
            ["Semantic similarity", "Compares the answer to the reference with embeddings."],
          ].map(([title, body]) => (
            <div key={title} className="rounded-xl border border-border bg-surface p-4">
              <p className="text-[13.5px] font-medium">{title}</p>
              <p className="mt-1.5 text-[12.5px] leading-relaxed text-fg-muted">{body}</p>
            </div>
          ))}
        </div>
        <p className="mt-4 text-[13px] leading-relaxed text-fg-muted">
          Scores are produced by running the harness. Nothing on this page is a
          pre-baked benchmark result.
        </p>
      </Section>

      {/* -- architecture ------------------------------------------------- */}
      <Section
        id="architecture"
        eyebrow="Architecture"
        title="Two deployables, one contract."
        lede="The frontend is a Next.js app on Vercel; the backend is FastAPI on Render. The browser never calls FastAPI directly — authenticated traffic is proxied through the frontend, which keeps the access token in an httpOnly cookie and removes CORS from the production path entirely."
      >
        <ArchitectureDiagram />

        <div className="mt-6 grid gap-3 sm:grid-cols-2">
          <div className="rounded-xl border border-border bg-surface p-5">
            <h3 className="text-[14px] font-semibold">Frontend · Next.js</h3>
            <ul className="mt-2.5 space-y-1.5 text-[13px] leading-relaxed text-fg-muted">
              <li>App Router, TypeScript, Tailwind, React Server Components</li>
              <li>Session held in an httpOnly cookie, never in localStorage</li>
              <li>One typed API client; components issue no raw fetch calls</li>
              <li>Server route handlers proxy to the backend with the bearer token</li>
            </ul>
          </div>
          <div className="rounded-xl border border-border bg-surface p-5">
            <h3 className="text-[14px] font-semibold">Backend · FastAPI</h3>
            <ul className="mt-2.5 space-y-1.5 text-[13px] leading-relaxed text-fg-muted">
              <li>Agent loop, tool registry and provider router behind one endpoint</li>
              <li>Ingestion pipeline: parse, OCR, chunk, embed, store</li>
              <li>JWT authentication and role-based access control per route</li>
              <li>Docker image, health check, and Ollama for local development</li>
            </ul>
          </div>
        </div>
      </Section>

      {/* -- project ------------------------------------------------------ */}
      <Section
        id="project"
        eyebrow="Project"
        title="Read the code."
        lede="The repository contains the backend, the frontend, the test suites, the evaluation harness and the deployment configuration."
      >
        <div className="flex flex-wrap items-center gap-3">
          <a href={REPO} target="_blank" rel="noreferrer" className={buttonClass("primary", "lg")}>
            <GithubIcon className="size-4" />
            View on GitHub
          </a>
          <Link href="/login" className={buttonClass("secondary", "lg")}>
            Open Anchor
          </Link>
        </div>
        <p className="mt-5 text-[13px] leading-relaxed text-fg-muted">
          Authentication in this build issues a token from a username and role without
          verifying a password, and the Chroma store persists to a local directory. Both
          are documented in the repository README under known limitations.
        </p>
      </Section>

      <footer className="border-t border-border py-8">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-5 sm:px-7">
          <span className="flex items-center gap-2.5 text-[13px] text-fg-muted">
            <Mark className="size-5" />
            Anchor
          </span>
          <a
            href={REPO}
            target="_blank"
            rel="noreferrer"
            className="font-mono text-[12px] text-fg-subtle transition-colors hover:text-fg-muted"
          >
            github.com/tdk-netsec14/Anchor
          </a>
        </div>
      </footer>
    </div>
  );
}

function PipelineStep({ n, title, body }: { n: string; title: string; body: string }) {
  return (
    <div className="flex gap-3.5 rounded-xl border border-border bg-surface p-4">
      <span className="flex size-6 shrink-0 items-center justify-center rounded-md bg-accent-soft font-mono text-[11px] font-semibold text-accent">
        {n}
      </span>
      <div>
        <p className="text-[13.5px] font-medium">{title}</p>
        <p className="mt-1 text-[12.5px] leading-relaxed text-fg-muted">{body}</p>
      </div>
    </div>
  );
}

/** An illustrative excerpt in the citation format Anchor actually emits. */
function ContextBlock() {
  return (
    <div className="overflow-hidden rounded-xl border border-border bg-surface">
      <div className="flex items-center gap-2 border-b border-border bg-surface-2 px-4 py-2">
        <span className="size-2 rounded-full bg-border-strong" />
        <span className="font-mono text-[11px] text-fg-subtle">retrieved context</span>
      </div>
      <pre className="overflow-x-auto px-4 py-3.5 font-mono text-[11.5px] leading-relaxed text-fg-muted">
{`[S1] source: leave_policy.pdf, page 4
(relevance 0.82, id leave_policy_p4_c2)

Employees accrue 1.75 days of paid leave
per month, capped at 25 days per calendar
year. Unused days may be carried into the
following year up to a maximum of 5 days
and must be used before 31 March.

[S2] source: leave_policy.pdf, page 7
(relevance 0.74, id leave_policy_p7_c1)

Carry-over beyond 5 days lapses and is
not paid out.`}
      </pre>
    </div>
  );
}

function ArchitectureDiagram() {
  const nodes = [
    { label: "Browser", note: "Next.js client" },
    { label: "Vercel", note: "route handlers · httpOnly cookie" },
    { label: "Render", note: "FastAPI · JWT + RBAC" },
  ];

  return (
    <div className="overflow-hidden rounded-xl border border-border bg-surface">
      <div className="flex flex-col items-stretch divide-y divide-border sm:flex-row sm:divide-x sm:divide-y-0">
        {nodes.map((node, index) => (
          <div key={node.label} className="flex flex-1 items-center gap-3 px-4 py-3.5">
            <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-accent-soft font-mono text-[11px] font-semibold text-accent">
              {index + 1}
            </span>
            <span className="min-w-0">
              <span className="block text-[13px] font-medium">{node.label}</span>
              <span className="block truncate font-mono text-[11px] text-fg-subtle">
                {node.note}
              </span>
            </span>
          </div>
        ))}
      </div>
      <div className="flex flex-col items-stretch divide-y divide-border border-t border-border bg-surface-2/50 sm:flex-row sm:divide-x sm:divide-y-0">
        {[
          { label: "LLM providers", note: "Groq · OpenAI · Gemini · Ollama" },
          { label: "Vector store", note: "ChromaDB collection" },
        ].map((node) => (
          <div key={node.label} className="flex-1 px-4 py-3.5">
            <span className="block text-[13px] font-medium text-fg-muted">{node.label}</span>
            <span className="block font-mono text-[11px] text-fg-subtle">{node.note}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
