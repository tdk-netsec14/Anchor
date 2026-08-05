import { NextResponse } from "next/server";

import { BackendError, callBackend } from "@/lib/backend";
import { setSession } from "@/lib/session";
import { SessionResponse } from "@/types/api";

/**
 * Create an account and its first workspace.
 *
 * Registration is refused by the backend when `AUTH_ALLOW_REGISTRATION` is
 * off, which is how an invite-only deployment closes this path without the
 * frontend needing to know the difference.
 */
export async function POST(request: Request) {
  let payload: { email?: string; password?: string; full_name?: string; workspace_name?: string };
  try {
    payload = await request.json();
  } catch {
    return json({ error: "invalid_request", message: "Expected a JSON body." }, 400);
  }

  const email = (payload.email ?? "").trim();
  const password = payload.password ?? "";
  const fullName = (payload.full_name ?? "").trim();

  if (!email || !password) {
    return json(
      { error: "invalid_request", message: "An email address and password are required." },
      400,
    );
  }

  const workspaceName = (payload.workspace_name ?? "").trim();

  try {
    const { body } = await callBackend({
      path: "/auth/register",
      method: "POST",
      body: JSON.stringify({
        email,
        password,
        full_name: fullName,
        ...(workspaceName ? { workspace_name: workspaceName } : {}),
      }),
      contentType: "application/json",
    });

    const session = await setSession(body as SessionResponse);
    return json(session, 201);
  } catch (err) {
    if (err instanceof BackendError) {
      return json(err.toApiError(), err.status);
    }
    throw err;
  }
}

function json(body: unknown, status = 200) {
  return NextResponse.json(body, { status, headers: { "Cache-Control": "no-store" } });
}
