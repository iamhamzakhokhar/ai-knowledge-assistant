export type Role = "owner" | "admin" | "member";
export type Team = { id: string; name: string; role: Role };
export type User = { id: string; email: string };
export type Member = User & { role: Role };
export type Session = { user: User; teams: Team[] };
export type Invite = { email: string; role: "admin" | "member"; token: string; expires_at: string };
export type ResponseShape =
  | "session"
  | "teams"
  | "team"
  | "members"
  | "member"
  | "invite"
  | "empty";

export const roleName: Record<Role, string> = { owner: "Owner", admin: "Admin", member: "Member" };

export function isId(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isRole(value: unknown): value is Role {
  return value === "owner" || value === "admin" || value === "member";
}

function parseUser(value: unknown): User | null {
  if (!isRecord(value) || !isId(value.id) || typeof value.email !== "string") return null;
  return { id: value.id, email: value.email };
}

function parseTeam(value: unknown): Team | null {
  if (!isRecord(value) || !isId(value.id) || typeof value.name !== "string" || !isRole(value.role))
    return null;
  return { id: value.id, name: value.name, role: value.role };
}

function parseMember(value: unknown): Member | null {
  const user = parseUser(value);
  if (!user || !isRecord(value) || !isRole(value.role)) return null;
  return { ...user, role: value.role };
}

function parseInvite(value: unknown): Invite | null {
  if (
    !isRecord(value) ||
    typeof value.email !== "string" ||
    typeof value.token !== "string" ||
    typeof value.expires_at !== "string" ||
    (value.role !== "admin" && value.role !== "member")
  )
    return null;
  return { email: value.email, role: value.role, token: value.token, expires_at: value.expires_at };
}

function parseList<T>(value: unknown, parseItem: (item: unknown) => T | null): T[] | null {
  if (!Array.isArray(value)) return null;
  const items: T[] = [];
  for (const item of value) {
    const parsed = parseItem(item);
    if (!parsed) return null;
    items.push(parsed);
  }
  return items;
}

export function parseSession(value: unknown): Session | null {
  if (!isRecord(value)) return null;
  const user = parseUser(value.user);
  const teams = parseList(value.teams, parseTeam);
  return user && teams ? { user, teams } : null;
}

export function parseRoster(value: unknown): { members: Member[] } | null {
  if (!isRecord(value)) return null;
  const members = parseList(value.members, parseMember);
  return members ? { members } : null;
}

export function parseWorkspaceResponse(shape: Exclude<ResponseShape, "empty">, value: unknown) {
  switch (shape) {
    case "session":
      return parseSession(value);
    case "team":
      return parseTeam(value);
    case "teams": {
      const teams = isRecord(value) ? parseList(value.teams, parseTeam) : null;
      return teams ? { teams } : null;
    }
    case "member":
      return parseMember(value);
    case "members":
      return parseRoster(value);
    case "invite":
      return parseInvite(value);
  }
}

export function teamUrl(id: string): string {
  return `/dashboard?team=${encodeURIComponent(id)}`;
}
