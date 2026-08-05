import "server-only";

import { cookies, headers } from "next/headers";

import { Session, SessionResponse } from "@/types/api";

/**
 * The Anchor session, held entirely in httpOnly cookies.
 *
 * Three cookies, and the split is deliberate:
 *
 * - `anchor_session`  the access token. Attached as a bearer header by the
 *   proxy, and nothing else ever reads it.
 * - `anchor_refresh`  the refresh token. Single-use: redeeming it mints a new
 *   pair, and presenting a redeemed one is treated as theft and revokes every
 *   session for that user. Kept in a separate cookie so the access path never
 *   has to touch it.
 * - `anchor_profile`  the descriptive fields the shell renders. Not a
 *   credential and not trusted for anything.
 *
 * None of them are readable from client JavaScript, so an XSS bug cannot
 * exfiltrate a token, and none of them are written to `localStorage` where any
 * injected script could reach them. Authorisation is still decided by FastAPI
 * on every request it serves; these cookies are transport, not a trust
 * boundary.
 */

const ACCESS_COOKIE = "anchor_session";
const REFRESH_COOKIE = "anchor_refresh";
const PROFILE_COOKIE = "anchor_profile";
const DEFAULT_TTL_SECONDS = 60 * 60;

type TokenClaims = {
  sub?: string;
  exp?: number;
  wid?: string;
  wrole?: string;
  email?: string;
};

/**
 * Mark the session cookies `Secure` whenever the request really did arrive over
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

export async function setSession(session: SessionResponse): Promise<Session> {
  const secure = await isSecureRequest();
  const store = await cookies();

  store.set(ACCESS_COOKIE, session.access_token, {
    httpOnly: true,
    // "lax" still sends the cookie on top-level navigation, so a deep link
    // into /assistant works, while blocking it on cross-site POSTs.
    sameSite: "lax",
    secure,
    path: "/",
    maxAge: session.expires_in,
  });

  // The refresh token outlives the access token on purpose — it is what makes
  // "stay signed in" work without ever putting a long-lived credential in a
  // request the user can be induced to send.
  store.set(REFRESH_COOKIE, session.refresh_token, {
    httpOnly: true,
    sameSite: "lax",
    secure,
    path: "/api/auth",
    maxAge: 60 * 60 * 24 * 30,
  });

  const profile: Session = {
    user_id: session.user_id,
    email: session.email,
    full_name: session.full_name,
    workspace_id: session.workspace_id,
    workspace_name: session.workspace_name,
    workspace_role: session.workspace_role,
    expires_at: Date.now() + session.expires_in * 1000,
  };
  store.set(PROFILE_COOKIE, JSON.stringify(profile), {
    httpOnly: true,
    sameSite: "lax",
    secure,
    path: "/",
    maxAge: session.expires_in,
  });

  return profile;
}

export async function clearSession(): Promise<void> {
  const store = await cookies();
  for (const name of [ACCESS_COOKIE, REFRESH_COOKIE, PROFILE_COOKIE]) {
    store.set(name, "", { httpOnly: true, path: "/", maxAge: 0 });
  }
}

export async function getToken(): Promise<string | null> {
  const store = await cookies();
  return store.get(ACCESS_COOKIE)?.value ?? null;
}

export async function getRefreshToken(): Promise<string | null> {
  const store = await cookies();
  return store.get(REFRESH_COOKIE)?.value ?? null;
}

/**
 * The principal for the current session, or null.
 *
 * The profile cookie is only a display cache, so it is validated against the
 * access token's own expiry rather than trusted on its own: a stale profile
 * left behind by a crashed request must not keep the shell rendering a signed
 * -in user after the token stopped working.
 */
export async function getSession(): Promise<Session | null> {
  const [token, raw] = await Promise.all([getToken(), readProfile()]);
  if (!token || !raw) return null;

  const claims = decodeClaims(token);
  if (!claims) return null;

  const now = Date.now();
  if (typeof claims.exp === "number" && claims.exp * 1000 <= now) return null;
  if (raw.expires_at <= now) return null;

  // The token is authoritative for identity and tenant: a profile that
  // disagrees with it is discarded rather than shown.
  if (claims.sub && claims.sub !== raw.user_id) return null;
  if (claims.wid && claims.wid !== raw.workspace_id) return null;

  return { ...raw, expires_at: typeof claims.exp === "number" ? claims.exp * 1000 : now + DEFAULT_TTL_SECONDS * 1000 };
}

async function readProfile(): Promise<Session | null> {
  const store = await cookies();
  const raw = store.get(PROFILE_COOKIE)?.value;
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Session;
    return parsed && typeof parsed.user_id === "string" ? parsed : null;
  } catch {
    return null;
  }
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
