import {
  documentUploadLimit,
  isId,
  parseWorkspaceResponse,
  type ResponseShape,
} from "@/lib/workspace";
import { requestInternalApi, sessionCookieHeader, sessionCookieName } from "@/lib/workspace-server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const maxJsonBytes = 16 * 1024;
const staticRoutes = new Map<string, ResponseShape>([
  ["POST auth/signup", "session"],
  ["POST auth/login", "session"],
  ["GET auth/session", "session"],
  ["POST auth/logout", "empty"],
  ["GET teams", "teams"],
  ["POST teams", "team"],
  ["POST teams/join", "team"],
]);

const errorMessages = new Map<number, string>([
  [400, "The request could not be read."],
  [401, "Sign in to continue."],
  [403, "You don’t have permission to do that."],
  [404, "The requested workspace resource is unavailable."],
  [409, "That change conflicts with the current workspace state."],
  [413, "The request is too large."],
  [415, "The request uses an unsupported format."],
  [422, "Check the details and try again."],
  [429, "Too many attempts. Wait a moment and try again."],
  [503, "The workspace service is temporarily unavailable. Try again shortly."],
]);

function operation(method: string, segments: string[]): ResponseShape | undefined {
  if (segments.some((segment) => !/^[a-z0-9-]+$/i.test(segment))) return undefined;
  const fixed = staticRoutes.get(`${method} ${segments.join("/")}`);
  if (fixed) return fixed;
  const [resource, team, collection, entity, action] = segments;
  if (resource !== "teams" || !isId(team)) return undefined;
  if (segments.length === 3) {
    if (collection === "members" && method === "GET") return "members";
    if (collection === "invites" && method === "POST") return "invite";
    if (collection === "documents" && method === "GET") return "documents";
    if (collection === "documents" && method === "POST") return "document";
  }
  if (segments.length === 4 && isId(entity)) {
    if (collection === "members" && method === "PATCH") return "member";
    if (collection === "members" && method === "DELETE") return "empty";
    if (collection === "documents" && method === "GET") return "documentDetail";
  }
  if (
    segments.length === 5 &&
    collection === "documents" &&
    isId(entity) &&
    action === "retry" &&
    method === "POST"
  ) {
    return "document";
  }
  return undefined;
}

function uncachedHeaders(): Headers {
  return new Headers({ "Cache-Control": "private, no-store" });
}

function failure(status: number, headers = uncachedHeaders()): Response {
  const knownStatus = errorMessages.has(status) ? status : 503;
  return Response.json({ error: errorMessages.get(knownStatus) }, { status: knownStatus, headers });
}

async function readBody(request: Request, limit: number): Promise<Uint8Array<ArrayBuffer> | null> {
  if (Number(request.headers.get("content-length")) > limit) return null;
  const reader = request.body?.getReader();
  if (!reader) return new Uint8Array(0);
  const chunks: Uint8Array[] = [];
  let bytes = 0;
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      bytes += chunk.value.byteLength;
      if (bytes > limit) {
        await reader.cancel();
        return null;
      }
      chunks.push(chunk.value);
    }
  } finally {
    reader.releaseLock();
  }
  const body = new Uint8Array(bytes);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return body;
}

async function handle(
  request: Request,
  context: { params: Promise<{ segments: string[] }> },
): Promise<Response> {
  const { segments } = await context.params;
  const shape = operation(request.method, segments);
  if (!shape || new URL(request.url).search) return failure(404);

  const outgoing = new Headers({ Accept: "application/json" });
  const origin = request.headers.get("origin");
  const cookie = await sessionCookieHeader();
  if (origin !== null) outgoing.set("Origin", origin);
  if (cookie !== null) outgoing.set("Cookie", cookie);
  let body: BodyInit | undefined;
  if (request.method !== "GET") {
    if (origin === null) return failure(403);
    const contentType = request.headers.get("content-type") ?? "";
    const uploading =
      request.method === "POST" && segments.length === 3 && segments[2] === "documents";
    const expected = uploading ? "application/octet-stream" : "application/json";
    if (contentType.split(";")[0].trim().toLowerCase() !== expected) return failure(415);
    outgoing.set("Content-Type", contentType);
    if (uploading) {
      const name = request.headers.get("x-document-name");
      if (!name || name.length > 765) return failure(422);
      outgoing.set("X-Document-Name", name);
    }
    try {
      const contents = await readBody(request, uploading ? documentUploadLimit : maxJsonBytes);
      if (contents === null) return failure(413);
      body = uploading ? contents : new TextDecoder("utf-8", { fatal: true }).decode(contents);
    } catch {
      return failure(400);
    }
  }

  try {
    const upstream = await requestInternalApi(`/api/v1/${segments.join("/")}`, {
      method: request.method,
      headers: outgoing,
      body,
    });
    const responseHeaders = uncachedHeaders();
    if (segments[0] === "auth") {
      for (const setCookie of upstream.headers.getSetCookie()) {
        if (setCookie.startsWith(`${sessionCookieName}=`))
          responseHeaders.append("Set-Cookie", setCookie);
      }
    }
    if (!upstream.ok) {
      // Validation responses can contain submitted field values. Return fixed messages.
      await upstream.body?.cancel();
      return failure(upstream.status, responseHeaders);
    }
    if (shape === "empty") {
      await upstream.body?.cancel();
      return upstream.status === 204
        ? new Response(null, { status: 204, headers: responseHeaders })
        : failure(503);
    }
    const result = parseWorkspaceResponse(shape, await upstream.json());
    return result
      ? Response.json(result, { status: upstream.status, headers: responseHeaders })
      : failure(503);
  } catch {
    return failure(503);
  }
}

export { handle as DELETE, handle as GET, handle as PATCH, handle as POST };
