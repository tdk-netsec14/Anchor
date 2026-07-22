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

import json
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from agent import rate_limit
from agent.agent import AgentOutcome, AnchorAgent
from agent.auth.principals import CurrentUser, CurrentUserDep
from agent.config import get_settings
from agent.db.access import scoped_one
from agent.db.models import Conversation, Message, QueryUsage
from agent.db.session import OptionalDbSession
from agent.guardrails.input_guard import check_query
from agent.observability.logger import get_logger, get_request_id
from agent.observability.metrics import registry
from agent.rag.retriever import Retriever, ScopeError
from agent.rate_limit import QUERY, check_daily_quota, principal_key
from agent.routing.providers.base import ProviderError
from agent.routing.router import AllProvidersFailed
from agent.schemas.query import QueryRequest, QueryResponse
from agent.tools.registry import ToolContext, ToolRegistry

router = APIRouter(tags=["query"])
log = get_logger(__name__)

#: The title a conversation carries until its first question replaces it.
DEFAULT_TITLE = "New conversation"


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
        "Retrieves relevant passages from the caller's workspace, routes the "
        "question to an appropriate model, lets it call tools, and returns a "
        "grounded, cited answer.\n\n"
        "Requires a bearer token. When `conversation_id` is supplied the turn "
        "is appended to that conversation, along with the model, latency, "
        "tokens, cost, sources and tool calls that produced it. When no "
        "provider can serve the request the call fails with 503 rather than "
        "returning a partial answer."
    ),
)
async def query(
    request: Request,
    payload: QueryRequest,
    user: CurrentUserDep,
    agent: Annotated[AnchorAgent, Depends(get_agent)],
    db: OptionalDbSession,
) -> QueryResponse:
    rate_limit.limiter.check(principal_key(request, user.user_id), QUERY)
    if user.workspace_id:
        check_daily_quota(user.workspace_id)

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

    tool_context = ToolContext(
        workspace_id=user.workspace_id,
        user_id=user.user_id,
        user_email=user.email or "",
        request_id=getattr(request.state, "request_id", "") or "",
        role=user.workspace_role.value if user.workspace_role else "",
    )

    try:
        outcome = await agent.answer(
            payload.query,
            force_model=payload.force_model,
            workspace_id=user.workspace_id,
            tool_context=tool_context,
        )
    except ScopeError as exc:
        # Only reachable when a database exists but the credential named no
        # workspace, which the auth layer already prevents. Treated as a
        # server misconfiguration rather than a user error.
        log.error("query.unscoped_retrieval_refused", context={"user_id": user.user_id})
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "no_workspace", "message": str(exc)},
        ) from exc
    except AllProvidersFailed as exc:
        log.error("query.all_providers_failed", context={"query_length": len(payload.query)})
        _record_usage(db, user, payload, request, error_type="all_providers_failed")
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
        _record_usage(db, user, payload, request, error_type=type(exc).__name__)
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
        _record_usage(db, user, payload, request, error_type=type(exc).__name__)
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

    _persist_turn(db, user, payload, request, outcome)

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


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def _record_usage(
    db: Session | None,
    user: CurrentUser,
    payload: QueryRequest,
    request: Request,
    *,
    error_type: str | None = None,
) -> None:
    """Append one `query_usage` row, best-effort.

    A failure to record usage must not turn a served answer into an error, so
    this swallows its own exceptions and logs. It is a no-op without a
    database, which is the development path.
    """
    if db is None or not user.workspace_id:
        return
    try:
        db.add(
            QueryUsage(
                workspace_id=user.workspace_id,
                user_id=user.user_id,
                request_id=getattr(request.state, "request_id", None) or "",
                model_used="",
                provider="",
                error_type=error_type,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        log.warning("query.usage_record_failed", context={"error_type": type(error_type).__name__})


def _persist_turn(
    db: Session | None,
    user: CurrentUser,
    payload: QueryRequest,
    request: Request,
    outcome: AgentOutcome,
) -> None:
    """Store the turn: the usage row, and both messages when a conversation
    was named.

    Best-effort throughout, for the same reason as :func:`_record_usage`. The
    question text is stored — it is the user's own words in their own
    workspace, and it is what makes the conversation history work.
    """
    if db is None or not user.workspace_id:
        return

    request_id = getattr(request.state, "request_id", None) or ""
    conversation_id = payload.conversation_id
    try:
        db.add(
            QueryUsage(
                workspace_id=user.workspace_id,
                user_id=user.user_id,
                conversation_id=conversation_id,
                request_id=request_id,
                model_used=outcome.model_used,
                provider=outcome.provider,
                routing_reason=outcome.routing_reason,
                prompt_tokens=outcome.prompt_tokens,
                completion_tokens=outcome.completion_tokens,
                cost_usd=outcome.cost_usd,
                latency_ms=outcome.latency_ms,
                sources_count=len(outcome.sources),
                tool_calls_json=json.dumps(
                    [
                        {"name": t.name, "ok": t.ok, "latency_ms": t.latency_ms}
                        for t in outcome.tool_calls
                    ]
                ),
                guardrail_flags_json=json.dumps(outcome.guardrail_flags),
                used_fallback=bool(outcome.fallbacks),
            )
        )
        if conversation_id:
            conversation = scoped_one(db, Conversation, conversation_id, user, what="conversation")
            db.add(
                Message(
                    conversation_id=conversation.id,
                    workspace_id=conversation.workspace_id,
                    role="user",
                    content=payload.query,
                )
            )
            db.add(
                Message(
                    conversation_id=conversation.id,
                    workspace_id=conversation.workspace_id,
                    role="assistant",
                    content=outcome.answer,
                    model_used=outcome.model_used,
                    provider=outcome.provider,
                    latency_ms=outcome.latency_ms,
                    prompt_tokens=outcome.prompt_tokens,
                    completion_tokens=outcome.completion_tokens,
                    cost_usd=outcome.cost_usd,
                    request_id=request_id,
                    sources_json=json.dumps(
                        [d.model_dump() for d in outcome.source_details]
                    ),
                    tool_calls_json=json.dumps([t.model_dump() for t in outcome.tool_calls]),
                    guardrail_flags_json=json.dumps(outcome.guardrail_flags),
                )
            )
            conversation.updated_at = datetime.now(UTC)
            if conversation.title == DEFAULT_TITLE:
                # First question names the thread, so the sidebar is not a
                # column of "New conversation".
                conversation.title = payload.query.strip()[:80] or DEFAULT_TITLE
        db.commit()
    except Exception:
        db.rollback()
        log.warning("query.turn_persist_failed", context={"error_type": "persist"}, exc_info=True)
