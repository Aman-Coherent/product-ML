import jwt from "jsonwebtoken";
import { getToken } from "next-auth/jwt";
import { NextRequest, NextResponse } from "next/server";

/**
 * Mints a short-lived HS256 JWT signed with the shared NEXTAUTH_SECRET so
 * the browser can call the FastAPI backend directly with
 * `Authorization: Bearer <token>`. The backend verifies this token with
 * the same secret (see backend/auth/jwt_verify.py) — no database lookup
 * needed on the Auth.js side for every backend request.
 *
 * We deliberately use the low-level `getToken()` (reads + decrypts the
 * session cookie directly from this request) instead of the higher-level
 * `auth()` helper. `auth()` internally memoizes its result per request via
 * React's `cache()`, and in Auth.js v5 beta this memoization has observed
 * cross-request bleed under concurrent Route Handler calls in dev — the
 * exact same session cookie would randomly decode successfully or come
 * back empty from one call to the next. `getToken()` has no such cache: it
 * decodes straight from `request`'s cookies every time, so it can't be
 * polluted by a different in-flight request.
 */
export async function GET(request: NextRequest) {
  const secret = process.env.NEXTAUTH_SECRET || process.env.AUTH_SECRET;
  if (!secret) {
    return NextResponse.json({ error: "Server misconfigured" }, { status: 500 });
  }

  const token = await getToken({ req: request, secret });
  const userId = (token?.id as string | undefined) ?? token?.sub;
  if (!userId || !token?.email) {
    return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
  }

  const backendToken = jwt.sign(
    { sub: userId, email: token.email, name: token.name },
    secret,
    { algorithm: "HS256", expiresIn: "1h" }
  );

  return NextResponse.json({ token: backendToken });
}
