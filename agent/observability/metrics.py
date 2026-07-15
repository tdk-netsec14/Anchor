"""In-process metrics registry.

Deliberately a small hand-rolled counter set rather than a Prometheus client:
the brief calls for real aggregate numbers over a demo deployment, and a
dependency-free registry is trivially correct. See the README for the migration
path to Prometheus/Grafana.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MetricsRegistry:
    """Thread-safe counters and latency aggregates for the running process."""

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    started_at: float = field(default_factory=time.time)

    total_queries: int = 0
    total_requests: int = 0
    total_errors: int = 0
    total_guardrail_blocks: int = 0
    total_tool_calls: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_cost_usd: float = 0.0
    total_fallbacks: int = 0
    total_documents_ingested: int = 0
    total_chunks_created: int = 0

    _latency_ms_total: float = 0.0
    _latency_ms_max: float = 0.0

    requests_by_model: dict[str, int] = field(default_factory=dict)
    requests_by_provider: dict[str, int] = field(default_factory=dict)
    requests_by_endpoint: dict[str, int] = field(default_factory=dict)
    errors_by_type: dict[str, int] = field(default_factory=dict)
    tool_calls_by_name: dict[str, int] = field(default_factory=dict)
    guardrail_flags: dict[str, int] = field(default_factory=dict)
    routing_reasons: dict[str, int] = field(default_factory=dict)
    recent_activity: list[dict[str, Any]] = field(default_factory=list)

    # -- recording ---------------------------------------------------------
    def record_query(
        self,
        *,
        latency_ms: float,
        model: str | None = None,
        provider: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: float = 0.0,
        tool_calls: list[str] | None = None,
        guardrail_flags: list[str] | None = None,
        routing_reason: str | None = None,
        query: str | None = None,
        request_id: str | None = None,
        sources_count: int = 0,
    ) -> None:
        tool_calls = tool_calls or []
        guardrail_flags = guardrail_flags or []
        with self._lock:
            self.total_queries += 1
            self._latency_ms_total += latency_ms
            self._latency_ms_max = max(self._latency_ms_max, latency_ms)
            self.total_prompt_tokens += prompt_tokens
            self.total_completion_tokens += completion_tokens
            self.total_cost_usd += cost_usd
            self.total_tool_calls += len(tool_calls)
            self._bump(self.requests_by_model, model or "unknown")
            self._bump(self.requests_by_provider, provider or "unknown")
            for name in tool_calls:
                self._bump(self.tool_calls_by_name, name)
            for flag in guardrail_flags:
                self._bump(self.guardrail_flags, flag)
            if routing_reason:
                self._bump(self.routing_reasons, routing_reason)

            activity_item = {
                "id": request_id or f"req_{int(time.time()*1000)}",
                "timestamp": time.time(),
                "query": query[:120] if query else "",
                "latency_ms": round(latency_ms, 2),
                "model": model or "unknown",
                "provider": provider or "unknown",
                "tokens_used": prompt_tokens + completion_tokens,
                "cost_usd": round(cost_usd, 6),
                "tool_calls": list(tool_calls),
                "guardrail_flags": list(guardrail_flags),
                "sources_count": sources_count,
                "routing_reason": routing_reason or "",
            }
            self.recent_activity.insert(0, activity_item)
            if len(self.recent_activity) > 50:
                self.recent_activity.pop()

    def get_recent_activity(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.recent_activity)

    def record_request(self, endpoint: str) -> None:
        with self._lock:
            self.total_requests += 1
            self._bump(self.requests_by_endpoint, endpoint)

    def record_error(self, error_type: str) -> None:
        with self._lock:
            self.total_errors += 1
            self._bump(self.errors_by_type, error_type)

    def record_fallback(self, from_provider: str, to_provider: str, reason: str) -> None:
        with self._lock:
            self.total_fallbacks += 1
            self._bump(self.errors_by_type, f"fallback:{from_provider}->{to_provider}:{reason}")

    def record_guardrail_block(self, flag: str) -> None:
        with self._lock:
            self.total_guardrail_blocks += 1
            self._bump(self.guardrail_flags, flag)

    def record_ingestion(self, chunks_created: int) -> None:
        with self._lock:
            self.total_documents_ingested += 1
            self.total_chunks_created += chunks_created

    @staticmethod
    def _bump(mapping: dict[str, int], key: str) -> None:
        mapping[key] = mapping.get(key, 0) + 1

    # -- reporting ---------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            answered = self.total_queries
            error_rate = (self.total_errors / self.total_requests) if self.total_requests else 0.0
            return {
                "uptime_seconds": round(time.time() - self.started_at, 3),
                "total_requests": self.total_requests,
                "total_queries": answered,
                "total_errors": self.total_errors,
                "error_rate": round(error_rate, 4),
                "total_guardrail_blocks": self.total_guardrail_blocks,
                "total_fallbacks": self.total_fallbacks,
                "latency_ms": {
                    "average": round(self._latency_ms_total / answered, 2) if answered else 0.0,
                    "max": round(self._latency_ms_max, 2),
                    "total": round(self._latency_ms_total, 2),
                },
                "tokens": {
                    "prompt": self.total_prompt_tokens,
                    "completion": self.total_completion_tokens,
                    "total": self.total_prompt_tokens + self.total_completion_tokens,
                },
                "cost": {
                    "total_usd": round(self.total_cost_usd, 6),
                    "average_usd_per_query": (
                        round(self.total_cost_usd / answered, 6) if answered else 0.0
                    ),
                },
                "requests_by_model": dict(self.requests_by_model),
                "requests_by_provider": dict(self.requests_by_provider),
                "requests_by_endpoint": dict(self.requests_by_endpoint),
                "tool_calls": {
                    "total": self.total_tool_calls,
                    "by_name": dict(self.tool_calls_by_name),
                },
                "ingestion": {
                    "documents": self.total_documents_ingested,
                    "chunks": self.total_chunks_created,
                },
                "guardrail_flags": dict(self.guardrail_flags),
                "routing_reasons": dict(self.routing_reasons),
                "errors_by_type": dict(self.errors_by_type),
            }

    def reset(self) -> None:
        with self._lock:
            self.started_at = time.time()
            self.total_queries = self.total_requests = self.total_errors = 0
            self.total_guardrail_blocks = self.total_tool_calls = 0
            self.total_prompt_tokens = self.total_completion_tokens = 0
            self.total_cost_usd = 0.0
            self.total_fallbacks = 0
            self.total_documents_ingested = self.total_chunks_created = 0
            self._latency_ms_total = 0.0
            self._latency_ms_max = 0.0
            for mapping in (
                self.requests_by_model,
                self.requests_by_provider,
                self.requests_by_endpoint,
                self.errors_by_type,
                self.tool_calls_by_name,
                self.guardrail_flags,
                self.routing_reasons,
            ):
                mapping.clear()


# Single process-wide registry. Metrics are intentionally per-process: a
# multi-replica deployment would need a shared backend (see README future work).
registry = MetricsRegistry()
