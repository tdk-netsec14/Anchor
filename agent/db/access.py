"""Tenant-scoped row lookups.

Every fetch of a tenant-owned row goes through one of these. They all take the
caller's workspace from their credential and put it in the ``WHERE`` clause, so
the scoping is part of the query rather than a check somebody might forget
afterwards. That is the whole reason they exist as shared helpers: the same
predicate written in six places is six chances to write it once.

A row that exists in another workspace raises 404, not 403. Distinguishing the
two would tell a caller that an id they guessed is real somewhere.
"""

from __future__ import annotations

from typing import Any, TypeVar

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from agent.auth.principals import CurrentUser

T = TypeVar("T")


def _not_found(what: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": "not_found", "message": f"No such {what} in this workspace."},
    )


def scoped_one(
    db: Session,
    model: type[T],
    row_id: str,
    user: CurrentUser,
    *,
    what: str = "record",
    **filters: Any,
) -> T:
    """Fetch one row by id, restricted to the caller's workspace.

    ``filters`` are additional equality predicates, for cases where the id
    alone is not enough to identify the row.
    """
    if not user.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "no_workspace",
                "message": "The credential names no workspace.",
            },
        )
    statement = select(model).where(
        model.id == row_id, model.workspace_id == user.workspace_id, **filters
    )
    # `.scalars()` unwraps the Row to the mapped instance. Without it this
    # returns a Row, whose attribute access does not proxy to the entity's
    # columns — so `row.id` raises AttributeError and every caller that
    # touches a column would 500.
    row = db.execute(statement).scalars().one_or_none()
    if row is None:
        raise _not_found(what)
    return row
