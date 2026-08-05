"use client";

import { Copy, KeyRound, Mail, Shield, Trash2, UserPlus } from "lucide-react";
import { useCallback, useState } from "react";

import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorNotice, Skeleton, Spinner } from "@/components/ui/feedback";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/hooks/useAuth";
import { useAsync } from "@/hooks/useAsync";
import { api, toApiError } from "@/lib/api-client";
import { hasWorkspaceRole, WORKSPACE_ROLES, WorkspaceRole } from "@/types/api";

/**
 * Members, invitations and API keys.
 *
 * The role selector is only rendered for someone who can actually change
 * roles, and the backend re-checks every one of these actions — a viewer who
 * edits the request by hand still gets a 403. Hiding the control is a
 * courtesy, not the control.
 */
export default function TeamPage() {
  const { session } = useAuth();
  const canManage = hasWorkspaceRole(session?.workspace_role, "admin");

  const members = useAsync(useCallback(() => api.members(), []));
  const invites = useAsync(useCallback(() => api.invites(), []));
  const keys = useAsync(useCallback(() => api.apiKeys(), []));

  return (
    <PageBody>
      <PageHeader
        title="Team"
        description={`Everyone in ${session?.workspace_name ?? "this workspace"}, and the API keys that can act on its behalf.`}
      />

      {!canManage ? (
        <Card className="mt-6">
          <CardBody>
            <p className="py-1 text-[13px] leading-relaxed text-fg-muted">
              You are <span className="font-medium text-fg">{session?.workspace_role}</span> in
              this workspace, so you can see the roster but not change it. Inviting members and
              issuing API keys require the <span className="font-medium text-fg">admin</span>{" "}
              role or higher.
            </p>
          </CardBody>
        </Card>
      ) : null}

      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <MembersCard
          canManage={canManage}
          currentUserId={session?.user_id ?? ""}
          state={members}
          onChanged={members.reload}
        />
        <InvitesCard canManage={canManage} state={invites} onChanged={invites.reload} />
      </div>

      <ApiKeysCard canManage={canManage} state={keys} onChanged={keys.reload} />
    </PageBody>
  );
}

type AsyncState<T> = { data: T | null; error: ReturnType<typeof toApiError> | null; loading: boolean };

/* -- members ------------------------------------------------------------- */

function MembersCard({
  canManage,
  currentUserId,
  state,
  onChanged,
}: {
  canManage: boolean;
  currentUserId: string;
  state: AsyncState<Awaited<ReturnType<typeof api.members>>>;
  onChanged: () => void;
}) {
  const [error, setError] = useState<string | null>(null);

  async function setRole(userId: string, role: WorkspaceRole) {
    setError(null);
    try {
      await api.setMemberRole(userId, role);
      onChanged();
    } catch (err) {
      setError(toApiError(err).message);
    }
  }

  async function remove(userId: string) {
    setError(null);
    try {
      await api.removeMember(userId);
      onChanged();
    } catch (err) {
      setError(toApiError(err).message);
    }
  }

  return (
    <Card className="overflow-hidden">
      <CardHeader>
        <CardTitle>Members</CardTitle>
      </CardHeader>
      {state.loading ? (
        <CardBody className="space-y-2">
          <Skeleton className="h-11 w-full" />
          <Skeleton className="h-11 w-full" />
        </CardBody>
      ) : state.error ? (
        <CardBody>
          <ErrorNotice error={state.error} onRetry={onChanged} />
        </CardBody>
      ) : state.data && state.data.length > 0 ? (
        <ul>
          {state.data.map((member) => (
            <li
              key={member.user_id}
              className="flex flex-wrap items-center gap-2 border-t border-border px-5 py-3"
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px] font-medium">
                  {member.full_name || member.email}
                  {member.user_id === currentUserId ? (
                    <span className="ml-1.5 text-[11px] text-fg-subtle">(you)</span>
                  ) : null}
                </span>
                <span className="block truncate text-[11.5px] text-fg-subtle">{member.email}</span>
              </span>

              {canManage && member.user_id !== currentUserId ? (
                <>
                  <select
                    aria-label={`Role for ${member.email}`}
                    value={member.role}
                    onChange={(e) => void setRole(member.user_id, e.target.value as WorkspaceRole)}
                    className="h-8 rounded-lg border border-border bg-surface px-2 text-[12.5px] text-fg"
                  >
                    {WORKSPACE_ROLES.map((role) => (
                      <option key={role} value={role}>
                        {role}
                      </option>
                    ))}
                  </select>
                  <Button
                    size="sm"
                    variant="danger"
                    aria-label={`Remove ${member.email}`}
                    onClick={() => void remove(member.user_id)}
                  >
                    <Trash2 className="size-3.5" />
                  </Button>
                </>
              ) : (
                <Badge tone={member.role === "owner" ? "accent" : "neutral"}>{member.role}</Badge>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState
          icon={<Shield className="size-6" />}
          title="No members"
          description="Invite a colleague to give them access to this workspace's documents and conversations."
        />
      )}

      {error ? (
        <CardBody className="pt-0">
          <p role="alert" className="text-[12.5px] text-danger">
            {error}
          </p>
        </CardBody>
      ) : null}
    </Card>
  );
}

/* -- invites ------------------------------------------------------------- */

function InvitesCard({
  canManage,
  state,
  onChanged,
}: {
  canManage: boolean;
  state: AsyncState<Awaited<ReturnType<typeof api.invites>>>;
  onChanged: () => void;
}) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<WorkspaceRole>("member");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [issued, setIssued] = useState<string | null>(null);

  async function invite() {
    setBusy(true);
    setError(null);
    setIssued(null);
    try {
      const result = await api.inviteMember(email.trim(), role);
      // The only time the full token is ever available. Shown once, then the
      // row below tracks it by id.
      setIssued(result.token);
      setEmail("");
      onChanged();
    } catch (err) {
      setError(toApiError(err).message);
    } finally {
      setBusy(false);
    }
  }

  async function revoke(id: string) {
    setError(null);
    try {
      await api.revokeInvite(id);
      onChanged();
    } catch (err) {
      setError(toApiError(err).message);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Invitations</CardTitle>
      </CardHeader>
      <CardBody>
        {canManage ? (
          <div className="flex flex-wrap items-end gap-2">
            <div className="min-w-[12rem] flex-1 space-y-1.5">
              <label htmlFor="invite-email" className="text-[12.5px] font-medium">
                Invite by email
              </label>
              <Input
                id="invite-email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="colleague@company.com"
              />
            </div>
            <select
              aria-label="Role for the new member"
              value={role}
              onChange={(e) => setRole(e.target.value as WorkspaceRole)}
              className="h-9 rounded-lg border border-border bg-surface px-2.5 text-[13px] text-fg"
            >
              {WORKSPACE_ROLES.filter((r) => r !== "owner").map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
            <Button variant="primary" onClick={invite} disabled={busy || !email.trim()}>
              {busy ? <Spinner /> : <UserPlus className="size-4" />}
              Invite
            </Button>
          </div>
        ) : (
          <p className="text-[13px] text-fg-muted">
            Only an admin can invite people into this workspace.
          </p>
        )}

        {issued ? (
          <div className="mt-4 rounded-lg border border-accent/30 bg-accent-soft/50 p-3">
            <p className="text-[12.5px] font-medium text-fg">
              Copy this invite token now — it is not shown again.
            </p>
            <div className="mt-2 flex items-center gap-2">
              <code className="min-w-0 flex-1 truncate rounded-md bg-surface px-2 py-1.5 font-mono text-[11.5px] text-fg">
                {issued}
              </code>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => void navigator.clipboard?.writeText(issued)}
              >
                <Copy className="size-3.5" />
                Copy
              </Button>
            </div>
          </div>
        ) : null}

        {error ? (
          <p role="alert" className="mt-3 text-[12.5px] text-danger">
            {error}
          </p>
        ) : null}

        <div className="mt-4">
          {state.loading ? (
            <Skeleton className="h-9 w-full" />
          ) : state.data && state.data.length > 0 ? (
            <ul className="divide-y divide-border rounded-lg border border-border">
              {state.data.map((invite) => (
                <li key={invite.id} className="flex items-center gap-2 px-3 py-2">
                  <Mail className="size-3.5 shrink-0 text-fg-subtle" />
                  <span className="min-w-0 flex-1 truncate text-[12.5px]">{invite.email}</span>
                  <Badge>{invite.role}</Badge>
                  {canManage ? (
                    <Button
                      size="sm"
                      variant="danger"
                      aria-label={`Revoke the invite for ${invite.email}`}
                      onClick={() => void revoke(invite.id)}
                    >
                      Revoke
                    </Button>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[12.5px] text-fg-subtle">No outstanding invitations.</p>
          )}
        </div>
      </CardBody>
    </Card>
  );
}

/* -- api keys ------------------------------------------------------------ */

function ApiKeysCard({
  canManage,
  state,
  onChanged,
}: {
  canManage: boolean;
  state: AsyncState<Awaited<ReturnType<typeof api.apiKeys>>>;
  onChanged: () => void;
}) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [issued, setIssued] = useState<string | null>(null);

  async function create() {
    setBusy(true);
    setError(null);
    setIssued(null);
    try {
      const result = await api.createApiKey(name.trim() || "Workspace key");
      setIssued(result.key);
      setName("");
      onChanged();
    } catch (err) {
      setError(toApiError(err).message);
    } finally {
      setBusy(false);
    }
  }

  async function revoke(id: string) {
    setError(null);
    try {
      await api.revokeApiKey(id);
      onChanged();
    } catch (err) {
      setError(toApiError(err).message);
    }
  }

  return (
    <Card className="mt-4">
      <CardHeader>
        <CardTitle>API keys</CardTitle>
      </CardHeader>
      <CardBody>
        <p className="text-[12.5px] leading-relaxed text-fg-muted">
          A key acts as an administrator in this workspace. Anchor stores only a hash, and the
          full secret is shown once at creation.
        </p>

        {canManage ? (
          <div className="mt-3 flex flex-wrap items-end gap-2">
            <div className="min-w-[12rem] flex-1 space-y-1.5">
              <label htmlFor="key-name" className="text-[12.5px] font-medium">
                Key name
              </label>
              <Input
                id="key-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="CI pipeline"
                maxLength={80}
              />
            </div>
            <Button variant="primary" onClick={create} disabled={busy}>
              {busy ? <Spinner /> : <KeyRound className="size-4" />}
              Create key
            </Button>
          </div>
        ) : null}

        {issued ? (
          <div className="mt-4 rounded-lg border border-warning/30 bg-warning-soft/50 p-3">
            <p className="text-[12.5px] font-medium text-fg">
              Copy this key now. Anchor keeps only a hash, so it cannot be shown again.
            </p>
            <div className="mt-2 flex items-center gap-2">
              <code className="min-w-0 flex-1 truncate rounded-md bg-surface px-2 py-1.5 font-mono text-[11.5px] text-fg">
                {issued}
              </code>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => void navigator.clipboard?.writeText(issued)}
              >
                <Copy className="size-3.5" />
                Copy
              </Button>
            </div>
          </div>
        ) : null}

        {error ? (
          <p role="alert" className="mt-3 text-[12.5px] text-danger">
            {error}
          </p>
        ) : null}

        <div className="mt-4">
          {state.loading ? (
            <Skeleton className="h-9 w-full" />
          ) : state.error ? (
            <ErrorNotice error={state.error} onRetry={onChanged} />
          ) : state.data && state.data.length > 0 ? (
            <ul className="divide-y divide-border rounded-lg border border-border">
              {state.data.map((key) => {
                const revoked = key.revoked_at !== null;
                return (
                  <li key={key.id} className="flex flex-wrap items-center gap-2 px-3 py-2">
                    <KeyRound className="size-3.5 shrink-0 text-fg-subtle" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[12.5px] font-medium">{key.name}</span>
                      <span className="block font-mono text-[11px] text-fg-subtle">
                        {key.key_prefix}… ·{" "}
                        {key.last_used_at
                          ? `used ${new Date(key.last_used_at).toLocaleDateString()}`
                          : "never used"}
                      </span>
                    </span>
                    {revoked ? (
                      <Badge tone="danger">revoked</Badge>
                    ) : canManage ? (
                      <Button size="sm" variant="danger" onClick={() => void revoke(key.id)}>
                        Revoke
                      </Button>
                    ) : (
                      <Badge tone="success">active</Badge>
                    )}
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="text-[12.5px] text-fg-subtle">No API keys have been issued.</p>
          )}
        </div>
      </CardBody>
    </Card>
  );
}
