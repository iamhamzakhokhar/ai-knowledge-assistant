export type Role = "owner" | "admin" | "member";
export type Team = { id: string; name: string; role: Role };
export type User = { id: string; email: string };
export type Member = User & { role: Role };
export type Session = { user: User; teams: Team[] };
export type Invite = { email: string; role: "admin" | "member"; token: string; expires_at: string };
export type ProcessingStatus = "queued" | "processing" | "completed" | "failed";
export type DocumentSummary = {
  id: string;
  name: string;
  format: "pdf" | "docx" | "markdown";
  status: ProcessingStatus;
  attempt: number;
  error: string | null;
  updated_at: string;
};
export type DocumentSection = { text: string; page: number | null; heading: string | null };
export type DocumentDetail = DocumentSummary & { sections: DocumentSection[] };
export type WebsiteSummary = {
  id: string;
  url: string;
  sitemap: boolean;
  status: ProcessingStatus;
  attempt: number;
  processed: number;
  error: string | null;
  updated_at: string;
};
export type WebsitePage = {
  url: string;
  final_url: string | null;
  status: "completed" | "skipped" | "failed";
  title: string | null;
  text: string | null;
  error: string | null;
};
export type WebsiteDetail = WebsiteSummary & {
  published_attempt: number | null;
  pages: WebsitePage[];
  last_attempt_pages: WebsitePage[];
};
export type ResponseShape =
  | "session"
  | "teams"
  | "team"
  | "members"
  | "member"
  | "invite"
  | "documents"
  | "document"
  | "documentDetail"
  | "websites"
  | "website"
  | "websiteDetail"
  | "empty";

export const roleName: Record<Role, string> = { owner: "Owner", admin: "Admin", member: "Member" };
export const documentUploadLimit = 5 * 1024 * 1024;

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

function isProcessingStatus(value: unknown): value is ProcessingStatus {
  return (
    value === "queued" || value === "processing" || value === "completed" || value === "failed"
  );
}

function isCount(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function isNullableText(value: unknown): value is string | null {
  return value === null || typeof value === "string";
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

function parseDocument(value: unknown): DocumentSummary | null {
  if (!isRecord(value) || !isId(value.id) || typeof value.name !== "string") return null;
  const { id, name, format, status, attempt, error, updated_at } = value;
  if (format !== "pdf" && format !== "docx" && format !== "markdown") return null;
  if (!isProcessingStatus(status) || !isCount(attempt)) return null;
  if (!isNullableText(error) || typeof updated_at !== "string") return null;
  return { id, name, format, status, attempt, error, updated_at };
}

function parseDocumentSection(value: unknown): DocumentSection | null {
  if (!isRecord(value)) return null;
  const { text, page, heading } = value;
  if (typeof text !== "string" || (heading !== null && typeof heading !== "string")) return null;
  if (page !== null && (typeof page !== "number" || !Number.isSafeInteger(page) || page < 1))
    return null;
  return { text, page, heading };
}

export function parseDocuments(value: unknown): { documents: DocumentSummary[] } | null {
  if (!isRecord(value)) return null;
  const documents = parseList(value.documents, parseDocument);
  return documents ? { documents } : null;
}

function parseWebsite(value: unknown): WebsiteSummary | null {
  if (!isRecord(value)) return null;
  const { id, url, sitemap, status, attempt, processed, error, updated_at } = value;
  if (
    !isId(id) ||
    typeof url !== "string" ||
    typeof sitemap !== "boolean" ||
    !isProcessingStatus(status) ||
    !isCount(attempt) ||
    !isCount(processed) ||
    !isNullableText(error) ||
    typeof updated_at !== "string"
  )
    return null;
  return { id, url, sitemap, status, attempt, processed, error, updated_at };
}

function parseWebsitePage(value: unknown): WebsitePage | null {
  if (!isRecord(value)) return null;
  const { url, final_url, status, title, text, error } = value;
  if (
    typeof url !== "string" ||
    !isNullableText(final_url) ||
    (status !== "completed" && status !== "skipped" && status !== "failed") ||
    !isNullableText(title) ||
    !isNullableText(text) ||
    !isNullableText(error)
  )
    return null;
  return { url, final_url, status, title, text, error };
}

export function parseWebsites(value: unknown): { websites: WebsiteSummary[] } | null {
  if (!isRecord(value)) return null;
  const websites = parseList(value.websites, parseWebsite);
  return websites ? { websites } : null;
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
    case "documents":
      return parseDocuments(value);
    case "document":
      return parseDocument(value);
    case "documentDetail": {
      const document = parseDocument(value);
      const sections = isRecord(value) ? parseList(value.sections, parseDocumentSection) : null;
      return document && sections ? { ...document, sections } : null;
    }
    case "websites":
      return parseWebsites(value);
    case "website":
      return parseWebsite(value);
    case "websiteDetail": {
      const website = parseWebsite(value);
      if (!website || !isRecord(value)) return null;
      const { published_attempt } = value;
      if (published_attempt !== null && (!isCount(published_attempt) || published_attempt < 1))
        return null;
      const pages = parseList(value.pages, parseWebsitePage);
      const last_attempt_pages = parseList(value.last_attempt_pages, parseWebsitePage);
      return pages && last_attempt_pages
        ? { ...website, published_attempt, pages, last_attempt_pages }
        : null;
    }
  }
}

export function teamUrl(id: string): string {
  return `/dashboard?team=${encodeURIComponent(id)}`;
}
