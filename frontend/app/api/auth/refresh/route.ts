import { NextResponse } from "next/server";

import { BackendError, callBackend } from "@/lib/backend";
import { clearSession, getRefreshToken, setSession } from "@/lib/session";
import { SessionResponse } from "@/types/api";

/**
 * Trade the refresh token for a new access token.
 *
 * Called by the proxy when FastAPI rejects an access token, never from the
 * browser. It has to be a server-side route because the refresh token is an
 * httpOnly cookie scoped to `/api/auth` — deliberately unreachable from the
 * rest of the app, so only these handlers can present it.
 */
export async function POST() {
  const refreshToken = await getRefreshToken();
  if (!refreshToken) {
    await clearSession();
    return NextResponse.json({ error: "no_session" }, { status: 401 });
  }

  try {
    const { body } = await callBackend({
      path: "/auth/refresh",
      method: "POST",
      body: JSON.stringify({ refresh_token: refreshToken }),
      contentType: "application/json",
    });
    const session = await setSession(body as SessionResponse);
    return NextResponse.json(session, { headers: { "Cache-Control": "no-store" } });
  } catch (err) {
    // A rejected refresh token means the session is genuinely over — the
    // backend also treats a replayed token as theft and revokes the rest.
    await clearSession();
    if (err instanceof BackendError) {
      return NextResponse.json(err.toApiError(), { status: err.status });
    }
    throw err;
  }
}
