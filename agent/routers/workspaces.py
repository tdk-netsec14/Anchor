"""Workspaces, membership and invitations.

A workspace is the tenant. Everything a user can see belongs to exactly one,
and the workspace on a credential is what decides which.

Roles, from most to least capable:

``OWNER``   full control, including deleting the workspace and transferring it.
``ADMIN``   manage members, documents, API keys and workspace settings.
``MEMBER``  ask questions, use tools, upload and read.
``VIEWER``  ask questions and read. Cannot change anything.

Invitations carry a single-use token that is returned exactly once, at
creation, and stored only as a hash. There is no mail provider in Anchor, so
the caller is responsible for delivering the link; the token is exposed in the
creation response for exactly that reason.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agent import audit, rate_limit
from agent.auth.principals import (
    CurrentUser,
    CurrentUserDep,
    require_active_workspace,
    require_workspace_role,
)
from agent.auth.service import create_workspace
from agent.db.base import as_utc
from agent.db.models import (
    Membership,
    User,
    Workspace,
    WorkspaceInvite,
    WorkspaceRole,
)
from agent.db.session import OptionalDbSession
from agent.observability.logger import get_logger
from agent.rate_limit import principal_key
from agent.security import generate_token, hash_token

router = APIRouter(prefix="/workspaces", tags=["workspaces"])
log = get_logger(__name__)

INVITE_TTL_DAYS = 14

_AdminDep = Annotated[CurrentUser, Depends(require_workspace_role(WorkspaceRole.ADMIN))]


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class MemberResponse(BaseModel):
    user_id: str
    email: str
    full_name: str
    role: WorkspaceRole
    is_active: bool
    joined_at: str
    last_login_at: str | None = None


class MemberRoleUpdate(BaseModel):
    role: WorkspaceRole


class InviteCreate(BaseModel):
    email: EmailStr
    role: WorkspaceRole = WorkspaceRole.MEMBER


class InviteResponse(BaseModel):
    id: str
    email: str
    role: WorkspaceRole
    expires_at: str
    #: The redemption token. Returned only here; Anchor stores its hash and
    #: cannot show it again.
    token: str


class InviteAccept(BaseModel):
    token: str = Field(min_length=8, max_length=200)


class WorkspaceDetail(BaseModel):
    id: str
    name: str
    slug: str
    is_personal: bool
    created_at: str
    member_count: int
    your_role: WorkspaceRole | None = None


def _require_db(db: Session | None) -> Session:
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "database_unavailable",
                "message": "Workspaces need a database. Set DATABASE_URL and run the migrations.",
            },
        )
    return db


def _require_workspace(db: Session, user: CurrentUser) -> Workspace:
    if not user.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "no_workspace",
                "message": "The credential names no workspace. Sign in to a workspace first.",
            },
        )
    row = db.get(Workspace, user.workspace_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "message": "No such workspace."},
        )
    return row


# ---------------------------------------------------------------------------
# Workspaces
# ---------------------------------------------------------------------------
@router.get("", response_model=list[WorkspaceDetail], summary="Your workspaces")
async def list_workspaces(user: CurrentUserDep, db: OptionalDbSession) -> list[WorkspaceDetail]:
    session = _require_db(db)
    rows = session.execute(
        select(Workspace, Membership.role)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .where(Membership.user_id == user.user_id)
        .order_by(Membership.created_at)
    ).all()
    counts = dict(
        session.execute(
            select(Membership.workspace_id, func.count(Membership.id)).group_by(
                Membership.workspace_id
            )
        ).all()
    )
    return [
        WorkspaceDetail(
            id=workspace.id,
            name=workspace.name,
            slug=workspace.slug,
            is_personal=workspace.is_personal,
            created_at=workspace.created_at.isoformat(),
            member_count=int(counts.get(workspace.id, 1)),
            your_role=role,
        )
        for workspace, role in rows
    ]


@router.post(
    "",
    response_model=WorkspaceDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create a workspace",
    description="The creating user becomes its OWNER.",
)
async def create_new_workspace(
    payload: WorkspaceCreate, user: CurrentUserDep, db: OptionalDbSession
) -> WorkspaceDetail:
    session = _require_db(db)
    workspace = create_workspace(
        session, name=payload.name, owner_email=user.email or "", is_personal=False
    )
    session.add(
        Membership(workspace_id=workspace.id, user_id=user.user_id, role=WorkspaceRole.OWNER)
    )
    session.commit()
    audit.record_event(
        session,
        action="workspace.create",
        actor_email=user.email or "",
        user_id=user.user_id,
        workspace_id=workspace.id,
        target_type="workspace",
        target_id=workspace.id,
    )
    return WorkspaceDetail(
        id=workspace.id,
        name=workspace.name,
        slug=workspace.slug,
        is_personal=workspace.is_personal,
        created_at=workspace.created_at.isoformat(),
        member_count=1,
        your_role=WorkspaceRole.OWNER,
    )


@router.get("/current", response_model=WorkspaceDetail, summary="The active workspace")
async def current_workspace(user: CurrentUserDep, db: OptionalDbSession) -> WorkspaceDetail:
    session = _require_db(db)
    workspace = _require_workspace(session, user)
    count = len(
        session.execute(select(Membership.id).where(Membership.workspace_id == workspace.id)).all()
    )
    return WorkspaceDetail(
        id=workspace.id,
        name=workspace.name,
        slug=workspace.slug,
        is_personal=workspace.is_personal,
        created_at=workspace.created_at.isoformat(),
        member_count=count,
        your_role=user.workspace_role,
    )


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------
@router.get("/members", response_model=list[MemberResponse], summary="List members")
async def list_members(user: CurrentUserDep, db: OptionalDbSession) -> list[MemberResponse]:
    session = _require_db(db)
    _require_workspace(session, user)
    rows = session.execute(
        select(User, Membership.role, Membership.created_at)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.workspace_id == user.workspace_id)
        .order_by(Membership.created_at)
    ).all()
    return [
        MemberResponse(
            user_id=account.id,
            email=account.email,
            full_name=account.full_name,
            role=role,
            is_active=account.is_active,
            joined_at=joined.isoformat(),
            last_login_at=account.last_login_at.isoformat() if account.last_login_at else None,
        )
        for account, role, joined in rows
    ]


@router.patch(
    "/members/{member_user_id}",
    response_model=MemberResponse,
    summary="Change a member's role",
)
async def update_member_role(
    request: Request,
    member_user_id: str,
    payload: MemberRoleUpdate,
    user: _AdminDep,
    db: OptionalDbSession,
) -> MemberResponse:
    session = _require_db(db)
    workspace_id = require_active_workspace(user)
    membership = _require_membership(session, member_user_id, user)

    _guard_role_change(user, membership, payload.role)

    membership.role = payload.role
    session.commit()
    audit.record_event(
        session,
        action="member.role_change",
        actor_email=user.email or "",
        user_id=user.user_id,
        workspace_id=workspace_id,
        target_type="user",
        target_id=member_user_id,
        detail={"new_role": payload.role.value},
    )
    return _member_response(session, member_user_id, workspace_id, payload.role)


def _guard_role_change(actor: CurrentUser, target: Membership, new_role: WorkspaceRole) -> None:
    """Refuse changes that would remove the last owner, or self-demote one.

    A workspace with no owner cannot be administered or deleted, so the last
    OWNER cannot be demoted or removed. Nor can an owner quietly demote
    themselves out of the ability to fix it.
    """
    if target.role != WorkspaceRole.OWNER or new_role == WorkspaceRole.OWNER:
        return
    if target.user_id == actor.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "cannot_demote_self",
                "message": "You cannot remove your own owner role. Ask another owner.",
            },
        )


@router.delete("/members/{member_user_id}", summary="Remove a member")
async def remove_member(
    member_user_id: str, user: _AdminDep, db: OptionalDbSession
) -> dict[str, str]:
    session = _require_db(db)
    membership = _require_membership(session, member_user_id, user)
    workspace_id = membership.workspace_id

    if membership.role == WorkspaceRole.OWNER and _owner_count(session, workspace_id) <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "last_owner",
                "message": "A workspace must keep at least one owner.",
            },
        )

    session.delete(membership)
    session.commit()
    audit.record_event(
        session,
        action="member.remove",
        actor_email=user.email or "",
        user_id=user.user_id,
        workspace_id=workspace_id,
        target_type="user",
        target_id=member_user_id,
    )
    return {"status": "removed", "user_id": member_user_id}


def _require_membership(session: Session, member_user_id: str, user: CurrentUser) -> Membership:
    row = session.execute(
        select(Membership).where(
            Membership.user_id == member_user_id,
            Membership.workspace_id == user.workspace_id,
        )
    ).scalars().one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "message": "That user is not a member here."},
        )
    return row


def _owner_count(session: Session, workspace_id: str) -> int:
    return int(
        session.execute(
            select(func.count(Membership.id)).where(
                Membership.workspace_id == workspace_id,
                Membership.role == WorkspaceRole.OWNER,
            )
        ).scalar_one()
    )


def _member_response(
    session: Session, user_id: str, workspace_id: str, role: WorkspaceRole
) -> MemberResponse:
    account = session.get(User, user_id)
    if account is None:  # pragma: no cover - membership implies a user row
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "message": "No such user."},
        )
    joined = session.execute(
        select(Membership.created_at).where(
            Membership.user_id == user_id, Membership.workspace_id == workspace_id
        )
    ).first()
    return MemberResponse(
        user_id=account.id,
        email=account.email,
        full_name=account.full_name,
        role=role,
        is_active=account.is_active,
        joined_at=joined[0].isoformat() if joined else "",
        last_login_at=account.last_login_at.isoformat() if account.last_login_at else None,
    )


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------
@router.post(
    "/invites",
    response_model=InviteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Invite someone",
    description=(
        "Creates a single-use invitation. The token is returned here and never "
        "again — only its hash is stored. Anchor sends no email, so deliver the "
        "link yourself."
    ),
)
async def create_invite(
    request: Request, payload: InviteCreate, user: _AdminDep, db: OptionalDbSession
) -> InviteResponse:
    session = _require_db(db)
    rate_limit.limiter.check(principal_key(request, user.user_id))

    email = payload.email.strip().lower()
    existing = session.execute(
        select(Membership)
        .join(User, User.id == Membership.user_id)
        .where(Membership.workspace_id == user.workspace_id, User.email == email)
    ).scalars().one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "already_a_member",
                "message": "That person is already a member of this workspace.",
            },
        )

    raw_token = generate_token()
    invite = WorkspaceInvite(
        workspace_id=user.workspace_id,
        email=email,
        role=payload.role,
        token_hash=hash_token(raw_token),
        invited_by_user_id=user.user_id,
        expires_at=datetime.now(UTC) + timedelta(days=INVITE_TTL_DAYS),
    )
    session.add(invite)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "invite_conflict", "message": "Could not create that invitation."},
        ) from exc

    audit.record_event(
        session,
        action="invite.create",
        actor_email=user.email or "",
        user_id=user.user_id,
        workspace_id=user.workspace_id,
        target_type="invite",
        target_id=invite.id,
        detail={"invitee": email, "role": payload.role.value},
    )
    return InviteResponse(
        id=invite.id,
        email=invite.email,
        role=invite.role,
        expires_at=invite.expires_at.isoformat(),
        token=raw_token,
    )


@router.post(
    "/invites/accept",
    response_model=WorkspaceDetail,
    summary="Redeem an invitation",
    description="Adds the signed-in user to the workspace the invitation names.",
)
async def accept_invite(
    request: Request, payload: InviteAccept, user: CurrentUserDep, db: OptionalDbSession
) -> WorkspaceDetail:
    session = _require_db(db)
    account = session.get(User, user.user_id)
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_token", "message": "The access token is not valid."},
        )

    invite = session.execute(
        select(WorkspaceInvite).where(WorkspaceInvite.token_hash == hash_token(payload.token))
    ).scalars().one_or_none()
    now = datetime.now(UTC)
    if invite is None or invite.accepted_at is not None or as_utc(invite.expires_at) <= now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_invite", "message": "That invitation is not valid."},
        )
    if invite.email != account.email:
        # The token alone is not enough: it has to be redeemed by the address
        # it was issued to, or a forwarded link would grant access.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "invite_email_mismatch",
                "message": "That invitation was issued to a different address.",
            },
        )

    already = session.execute(
        select(Membership).where(
            Membership.user_id == user.user_id, Membership.workspace_id == invite.workspace_id
        )
    ).scalars().one_or_none()
    if already is None:
        session.add(
            Membership(workspace_id=invite.workspace_id, user_id=user.user_id, role=invite.role)
        )
    invite.accepted_at = now
    session.commit()

    workspace = session.get(Workspace, invite.workspace_id)
    audit.record_event(
        session,
        action="invite.accept",
        actor_email=account.email,
        user_id=user.user_id,
        workspace_id=invite.workspace_id,
        target_type="workspace",
        target_id=invite.workspace_id,
        detail={"role": invite.role.value},
    )
    return WorkspaceDetail(
        id=workspace.id,
        name=workspace.name,
        slug=workspace.slug,
        is_personal=workspace.is_personal,
        created_at=workspace.created_at.isoformat(),
        member_count=len(
            session.execute(
                select(Membership.id).where(Membership.workspace_id == invite.workspace_id)
            ).all()
        ),
        your_role=invite.role,
    )


@router.get("/invites", summary="Outstanding invitations")
async def list_invites(user: _AdminDep, db: OptionalDbSession) -> list[dict[str, Any]]:
    session = _require_db(db)
    rows = session.execute(
        select(WorkspaceInvite)
        .where(
            WorkspaceInvite.workspace_id == user.workspace_id,
            WorkspaceInvite.accepted_at.is_(None),
        )
        .order_by(WorkspaceInvite.created_at.desc())
    ).scalars()
    now = datetime.now(UTC)
    return [
        {
            "id": row.id,
            "email": row.email,
            "role": row.role.value,
            "expires_at": row.expires_at.isoformat(),
            "expired": as_utc(row.expires_at) <= now,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@router.delete("/invites/{invite_id}", summary="Revoke an invitation")
async def revoke_invite(invite_id: str, user: _AdminDep, db: OptionalDbSession) -> dict[str, str]:
    session = _require_db(db)
    invite = session.execute(
        select(WorkspaceInvite).where(
            WorkspaceInvite.id == invite_id, WorkspaceInvite.workspace_id == user.workspace_id
        )
    ).scalars().one_or_none()
    if invite is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "message": "No such invitation."},
        )
    session.delete(invite)
    session.commit()
    audit.record_event(
        session,
        action="invite.revoke",
        actor_email=user.email or "",
        user_id=user.user_id,
        workspace_id=user.workspace_id,
        target_type="invite",
        target_id=invite_id,
    )
    return {"status": "revoked", "invite_id": invite_id}
