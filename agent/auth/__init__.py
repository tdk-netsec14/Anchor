"""Authentication: token primitives, principals, and the account lifecycle.

The names exported here are the ones the rest of Anchor imports. They are
re-exported rather than defined locally so that ``from agent.auth import
create_access_token`` keeps working, which is how the original test suite and
the developer token command reach it.
"""

from agent.auth.principals import (
    AdminUserDep,
    CurrentUser,
    CurrentUserDep,
    Role,
    deny_unknown_workspace,
    get_current_user,
    optional_user,
    require_active_workspace,
    require_role,
    require_workspace_role,
)
from agent.auth.tokens import (
    TokenError,
    create_access_token,
    decode_access_token,
)

__all__ = [
    "AdminUserDep",
    "CurrentUser",
    "CurrentUserDep",
    "Role",
    "TokenError",
    "create_access_token",
    "decode_access_token",
    "deny_unknown_workspace",
    "get_current_user",
    "optional_user",
    "require_active_workspace",
    "require_role",
    "require_workspace_role",
]
