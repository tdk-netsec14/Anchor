import { NextResponse } from "next/server";

import { callBackend } from "@/lib/backend";
import { clearSession, getRefreshToken } from "@/lib/session";

/**
 * End the session.
 *
 * The refresh token is revoked on the backend first, so the session is dead
 * server-side and not merely forgotten by the browser. The cookies are cleared
 * either way: a logout that failed to reach the API must still sign the person
 * out on this device, which is what the caller is actually asking for.
 */
export async function POST() {
  const refreshToken = await getRefreshToken();
  try {
    if (refreshToken) {
      await callBackend({
        path: "/auth/logout",
        method: "POST",
        body: JSON.stringify({ refresh_token: refreshToken }),
        contentType: "application/json",
      });
    }
  } catch {
    // Best-effort. The endpoint is idempotent server-side, and losing the
    // cookie still removes this device's copy of the credential.
  } finally {
    await clearSession();
  }
  return NextResponse.json({ status: "signed_out" });
}
