"""``POST /query`` - the main entry point.

Order of operations, and why:

1. screen the input (cheap rejection before any expensive work);
2. route and answer through the agent loop;
3. apply output guardrails (inside the agent);
4. record metrics and emit one structured log line with the full correlation
   fields - model, latency, tokens, cost, guardrail flags.

The router translates internal failures into safe responses. A provider
exception never reaches the caller.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from agent.agent import AnchorAgent
from agent.auth import CurrentUserDep
from agent.config import get_settings
from agent.guardrails.input_guard import check_query
from agent.observability.logger import get_logger, get_request_id
from agent.observability.metrics import registry
from agent.rag.retriever import Retriever
from agent.routing.providers.base import ProviderError
from agent.routing.router import AllProvidersFailed
from agent.schemas.query import QueryRequest, QueryResponse
from agent.tools.registry import ToolRegistry

router = APIRouter(tags=["query"])
log = get_logger(__name__)


def build_tool_registry(
    retriever: Retriever | None = None,
    tickets_dir: str | Path | None = None,
) -> ToolRegistry:
    """Construct the application's tool set.

    The retriever is passed in so the ``search_kb`` tool and the agent share
    one instance - two retrievers would mean two independent configurations of
    the same knowledge base.
    """
    from agent.tools.calculator_tool import CalculatorTool
    from agent.tools.create_ticket_tool import CreateTicketTool
    from agent.tools.search_kb_tool import SearchKbTool

    tools = ToolRegistry()
    tools.register_all(
        [SearchKbTool(retriever), CalculatorTool(), CreateTicketTool(tickets_dir)]
    )
    return tools


@lru_cache(maxsize=1)
def get_agent() -> AnchorAgent:
    """The process-wide agent.

    Cached deliberately: building it per request would create a new retriever
    (and therefore a new view of the knowledge base) on every call, and rebuild
    the tool set for no benefit. The underlying embedder and vector store are
    themselves process-wide singletons, so this only fixes the wiring.
    """
    from agent.routing.router import get_router

    retriever = Retriever()
    return AnchorAgent(
        router=get_router(),
        registry=build_tool_registry(retriever),
        retriever=retriever,
    )


@router.post(
    "/query",
    response_model=QueryResponse,
    summary="Ask the knowledge base a question",
    description=(
        "Retrieves relevant passages, routes the question to an appropriate "
        "model, lets it call tools, and returns a grounded, cited answer.\n\n"
        "Requires a bearer token. When no provider can serve the request the "
        "call fails with 503 rather than returning a partial answer."
    ),
)
async def query(
    request: Request,
    payload: QueryRequest,
    user: CurrentUserDep,
    agent: Annotated[AnchorAgent, Depends(get_agent)],
) -> QueryResponse:
    check = check_query(payload.query)
    if not check.allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "input_rejected",
                "message": check.reason,
                "guardrail_flags": check.flags,
            },
        )

    try:
        outcome = await agent.answer(payload.query, force_model=payload.force_model)
    except AllProvidersFailed as exc:
        log.error("query.all_providers_failed", context={"query_length": len(payload.query)})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "no_provider_available",
                "message": (
                    "No language model provider is currently able to answer. "
                    "Please try again shortly."
                ),
            },
        ) from exc
    except ProviderError as exc:
        # A non-retryable error raised before any call went out is a bad
        # request - an unknown provider in `force_model`, for instance - not a
        # gateway failure. The message is safe to show: the router builds it
        # from configuration, not from a vendor response.
        if not exc.retryable:
            log.warning("query.invalid_provider_request", context={"reason": str(exc)})
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": "invalid_provider", "message": str(exc)},
            ) from exc
        log.error(
            "query.provider_error",
            context={"error_type": type(exc).__name__},
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": "provider_error",
                "message": "The language model provider could not serve this request.",
            },
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - the boundary must not leak internals
        registry.record_error(type(exc).__name__)
        log.error("query.unhandled_error", context={"error_type": type(exc).__name__}, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "internal_error",
                "message": "Something went wrong while answering the question.",
            },
        ) from exc

    settings = get_settings()
    registry.record_query(
        latency_ms=outcome.latency_ms,
        model=outcome.model_used,
        provider=outcome.provider,
        prompt_tokens=outcome.prompt_tokens,
        completion_tokens=outcome.completion_tokens,
        cost_usd=outcome.cost_usd,
        tool_calls=[t.name for t in outcome.tool_calls],
        guardrail_flags=outcome.guardrail_flags,
        routing_reason=outcome.routing_reason,
        query=payload.query,
        request_id=getattr(request.state, "request_id", None),
        sources_count=len(outcome.sources),
    )

    log.info(
        "query.completed",
        context={
            "user_id": user.user_id,
            "role": user.role,
            "model_used": outcome.model_used,
            "provider": outcome.provider,
            "routing_reason": outcome.routing_reason,
            "latency_ms": round(outcome.latency_ms, 2),
            "tokens_used": outcome.tokens_used,
            "estimated_cost_usd": outcome.cost_usd,
            "guardrail_flags": outcome.guardrail_flags,
            "tool_calls": [t.name for t in outcome.tool_calls],
            "retrieval_hits": outcome.retrieval_hits,
            "top_score": round(outcome.top_score, 4),
            "fallbacks": outcome.fallbacks,
            "env": settings.ENVIRONMENT,
        },
    )

    return QueryResponse(
        answer=outcome.answer,
        sources=outcome.sources,
        source_details=outcome.source_details,
        model_used=outcome.model_used,
        tool_calls=outcome.tool_calls,
        latency_ms=round(outcome.latency_ms, 2),
        tokens_used=outcome.tokens_used,
        estimated_cost_usd=outcome.cost_usd,
        guardrail_flags=outcome.guardrail_flags,
        request_id=get_request_id(),
        provider=outcome.provider,
        routing_reason=outcome.routing_reason,
        prompt_tokens=outcome.prompt_tokens,
        completion_tokens=outcome.completion_tokens,
        fallbacks=outcome.fallbacks,
        session_id=payload.session_id,
    )
