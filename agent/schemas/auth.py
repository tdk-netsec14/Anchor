"""Request and response models for the authentication endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, EmailStr, Field

from agent.db.models import WorkspaceRole


class RegisterRequest(BaseModel):
    email: EmailStr = Field(description="Login address. Stored lowercased.")
    password: str = Field(min_length=1, max_length=200, description="Never logged or returned.")
    full_name: str = Field(default="", max_length=120)
    workspace_name: str | None = Field(
        default=None,
        max_length=120,
        description="Name for the workspace created alongside the account.",
    )


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=200)


class SessionResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Access token lifetime in seconds.")
    user_id: str
    email: str
    full_name: str
    workspace_id: str
    workspace_name: str
    workspace_role: WorkspaceRole


class UserResponse(BaseModel):
    user_id: str
    email: str
    full_name: str
    created_at: str
    last_login_at: str | None = None


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetResponse(BaseModel):
    #: Present only when the deployment has no mail provider configured, in
    #: which case the token is returned so a developer can complete the flow.
    #: A production deployment leaves this null and delivers the link itself.
    reset_token: str | None = None
    message: str


class PasswordResetConfirm(BaseModel):
    token: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)


class WorkspaceSummary(BaseModel):
    id: str
    name: str
    slug: str
    role: WorkspaceRole
    is_personal: bool
    created_at: str
