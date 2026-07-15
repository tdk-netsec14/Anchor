import "server-only";

import { cookies, headers } from "next/headers";

import { Role, Session } from "@/types/api";

/**
 * The Anchor access token lives here, in an httpOnly cookie.
 *
 * It is never exposed to client JavaScript, so an XSS bug cannot read it, and
 * it is never written to `localStorage` where any injected script could
 * exfiltrate it. The client learns only the principal (`/api/auth/session`),
 * and every privileged call is re-authorised by the backend on its own terms —
 * the cookie is a transport detail, not a trust boundary.
 */

const COOKIE = "anchor_session";
const DEFAULT_TTL_SECONDS = 60 * 60;

type TokenClaims = {
  sub?: string;
  role?: string;
  exp?: number;
};

/**
 * Mark the session cookie `Secure` whenever the request really did arrive over
 * TLS, and not otherwise.
 *
 * `NODE_ENV === "production"` alone is the wrong signal: `next start` on a
 * laptop is "production" over plain HTTP, so a Secure cookie is silently never
 * sent back and every authenticated call 401s. Browsers paper over this by
 * treating http://localhost as a secure context, but curl, Postman and test
 * clients do not. Behind Vercel the forwarded protocol is authoritative.
 */
async function isSecureRequest(): Promise<boolean> {
  const store = await headers();
  const forwarded = store.get("x-forwarded-proto");
  if (forwarded) return forwarded.split(",")[0].trim() === "https";

  const host = (store.get("host") ?? "").split(":")[0];
  if (host === "localhost" || host === "127.0.0.1" || host === "::1") return false;

  return process.env.NODE_ENV === "production";
}

export async function setSessionCookie(
  token: string,
  role: Role,
  expiresIn: number,
): Promise<void> {
  const store = await cookies();
  store.set(COOKIE, token, {
    httpOnly: true,
    // "lax" still sends the cookie on top-level navigation, so a deep link
    // into /assistant works, while blocking it on cross-site POSTs.
    sameSite: "lax",
    secure: await isSecureRequest(),
    path: "/",
    maxAge: expiresIn > 0 ? expiresIn : DEFAULT_TTL_SECONDS,
  });
}

export async function clearSessionCookie(): Promise<void> {
  const store = await cookies();
  store.set(COOKIE, "", { httpOnly: true, path: "/", maxAge: 0 });
}

export async function getToken(): Promise<string | null> {
  const store = await cookies();
  return store.get(COOKIE)?.value ?? null;
}

/**
 * Read the principal out of the token for display and for gating the shell.
 *
 * The payload is decoded, not verified: verifying is FastAPI's job on every
 * request it serves, and duplicating signature checks here would mean two
 * places to keep the secret in sync. Nothing security-relevant is decided
 * from this value alone.
 */
export async function getSession(): Promise<Session | null> {
  const token = await getToken();
  if (!token) return null;

  const claims = decodeClaims(token);
  if (!claims) return null;

  if (typeof claims.exp === "number" && claims.exp * 1000 <= Date.now()) {
    return null;
  }

  const role: Role = claims.role === "admin" ? "admin" : "user";
  return {
    user_id: claims.sub ?? "unknown",
    role,
    expires_at: (claims.exp ?? Math.floor(Date.now() / 1000) + DEFAULT_TTL_SECONDS) * 1000,
  };
}

function decodeClaims(token: string): TokenClaims | null {
  const segment = token.split(".")[1];
  if (!segment) return null;
  try {
    const json = Buffer.from(segment, "base64url").toString("utf8");
    return JSON.parse(json) as TokenClaims;
  } catch {
    return null;
  }
}
