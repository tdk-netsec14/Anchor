import { NextResponse } from "next/server";

import { getSession } from "@/lib/session";

/**
 * The principal for the current session, or null. This is the only session
 * information the client ever sees — never the token itself.
 */
export async function GET() {
  const session = await getSession();
  return NextResponse.json(session, {
    status: session ? 200 : 401,
    headers: { "Cache-Control": "no-store" },
  });
}
