import "server-only";

import { cookies } from "next/headers";

export const sessionCookieName = "aka_session";

function internalApiOrigin(): string {
  const url = new URL(process.env.API_INTERNAL_URL || "http://127.0.0.1:8000");
  if (
    (url.protocol !== "http:" && url.protocol !== "https:") ||
    url.username ||
    url.password ||
    url.pathname !== "/" ||
    url.search ||
    url.hash
  ) {
    throw new Error("Invalid internal API origin");
  }
  return url.origin;
}

export async function sessionCookieHeader(): Promise<string | null> {
  const session = (await cookies()).get(sessionCookieName)?.value;
  return session ? `${sessionCookieName}=${encodeURIComponent(session)}` : null;
}

export function requestInternalApi(
  path: string,
  options: Pick<RequestInit, "method" | "headers" | "body"> = {},
): Promise<Response> {
  return fetch(`${internalApiOrigin()}${path}`, {
    ...options,
    cache: "no-store",
    redirect: "error",
    signal: AbortSignal.timeout(5000),
  });
}
