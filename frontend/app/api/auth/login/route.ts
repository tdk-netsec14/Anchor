import { NextResponse } from "next/server";

import { BackendError, callBackend } from "@/lib/backend";
import { setSessionCookie } from "@/lib/session";
import { ApiErrorBody, Role, TokenResponse } from "@/types/api";

/**
 * Exchange a username and role for an Anchor JWT and store it in an httpOnly
 * cookie. The token is returned to the server only — it is never echoed into
 * the browser's JavaScript context.
 */
export async function POST(request: Request) {
  let payload: { username?: string; role?: Role };
  try {
    payload = await request.json();
  } catch {
    return json({ error: "invalid_request", message: "Expected a JSON body." }, 400);
  }

  const username = (payload.username ?? "").trim();
  const role: Role = payload.role === "admin" ? "admin" : "user";

  if (!username) {
    return json(
      { error: "invalid_request", message: "A username is required." } satisfies ApiErrorBody,
      400,
    );
  }

  try {
    const { body } = await callBackend({
      path: "/auth/token",
      method: "POST",
      body: JSON.stringify({ username, role }),
      contentType: "application/json",
    });

    const token = body as TokenResponse;
    await setSessionCookie(token.access_token, token.role, token.expires_in);

    return json({
      user_id: username,
      role: token.role,
      expires_at: Date.now() + token.expires_in * 1000,
    });
  } catch (err) {
    if (err instanceof BackendError) {
      return json(err.toApiError(), err.status);
    }
    throw err;
  }
}

function json(body: unknown, status = 200) {
  return NextResponse.json(body, { status });
}
