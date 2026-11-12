import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { Brand } from "@/components/brand";
import { DocumentsPanel } from "@/components/documents-panel";
import { Icon } from "@/components/icons";
import { InviteForm, MembershipActions, SignOutButton, TeamForm } from "@/components/team-forms";
import { WebsitesPanel } from "@/components/websites-panel";
import {
  parseDocuments,
  parseRoster,
  parseSession,
  parseWebsites,
  roleName,
  type Team,
  teamUrl,
} from "@/lib/workspace";
import { requestInternalApi, sessionCookieHeader } from "@/lib/workspace-server";

export const metadata: Metadata = { title: "Workspace overview", robots: { index: false } };
export const dynamic = "force-dynamic";

async function readWorkspace<T>(
  path: string,
  cookie: string,
  parse: (data: unknown) => T | null,
): Promise<{ status: number; data: T | null }> {
  try {
    const response = await requestInternalApi(path, {
      headers: { Cookie: cookie, Accept: "application/json" },
    });
    if (!response.ok) {
      await response.body?.cancel();
      return { status: response.status, data: null };
    }
    const data = parse(await response.json());
    return { status: data ? response.status : 503, data };
  } catch {
    return { status: 503, data: null };
  }
}

function WorkspaceUnavailable() {
  return (
    <div className="auth-layout">
      <header className="auth-header page-width">
        <Brand />
      </header>
      <main className="auth-main page-width" id="main-content">
        <div className="auth-card">
          <h1>Workspace unavailable</h1>
          <p>The workspace service could not be reached. Try again shortly.</p>
          <div className="workspace-retry">
            <a href="/dashboard" className="button button-primary">
              Retry workspace
            </a>
            <Link href="/login" className="text-link">
              Go to sign in
            </Link>
          </div>
        </div>
      </main>
    </div>
  );
}

function TeamNavigation({ teams, selected }: { teams: Team[]; selected?: Team }) {
  return (
    <nav aria-label="Your teams" className="dashboard-nav workspace-teams">
      {teams.map((team) => (
        <Link
          key={team.id}
          href={teamUrl(team.id)}
          prefetch={false}
          aria-current={team.id === selected?.id ? "page" : undefined}
          className={team.id === selected?.id ? "active" : undefined}
        >
          <Icon name="layers" />
          <span>{team.name}</span>
        </Link>
      ))}
    </nav>
  );
}

export default async function DashboardPage({
  searchParams,
}: {
  searchParams: Promise<{ team?: string | string[] }>;
}) {
  const cookie = await sessionCookieHeader();
  if (!cookie) redirect("/login");
  const session = await readWorkspace("/api/v1/auth/session", cookie, parseSession);
  if (session.status === 401) redirect("/login");
  if (!session.data) return <WorkspaceUnavailable />;

  const { user, teams } = session.data;
  const requestedTeam = (await searchParams).team;
  const selected =
    requestedTeam === undefined ? teams[0] : teams.find((team) => team.id === requestedTeam);
  const [roster, documents, websites] = selected
    ? await Promise.all([
        readWorkspace(`/api/v1/teams/${selected.id}/members`, cookie, parseRoster),
        readWorkspace(`/api/v1/teams/${selected.id}/documents`, cookie, parseDocuments),
        readWorkspace(`/api/v1/teams/${selected.id}/websites`, cookie, parseWebsites),
      ])
    : [null, null, null];
  if ([roster, documents, websites].some((result) => result?.status === 401)) redirect("/login");

  return (
    <div className="dashboard-layout workspace-layout">
      <aside className="dashboard-sidebar">
        <Brand />
        <span className="sidebar-label">YOUR TEAMS</span>
        <TeamNavigation teams={teams} selected={selected} />
        {teams.length === 0 && <p className="workspace-no-teams">No teams yet</p>}
        <Link href="/project" className="workspace-project-link text-link">
          Project overview <Icon name="arrow" />
        </Link>
      </aside>
      <div className="dashboard-body">
        <header className="dashboard-topbar workspace-topbar">
          <span>KnowledgeDesk workspace</span>
          <div className="workspace-account">
            <span>{user.email}</span>
            <SignOutButton />
          </div>
        </header>
        <main className="dashboard-main workspace-main" id="main-content">
          <h1>Workspace overview</h1>
          <div className="workspace-team-forms">
            <TeamForm mode="create" />
            <TeamForm mode="join" />
          </div>
          {!selected && (
            <section className="dashboard-panel workspace-panel" aria-labelledby="no-team-heading">
              <h2 id="no-team-heading">{requestedTeam ? "Team unavailable" : "Your teams"}</h2>
              <p>
                {requestedTeam
                  ? "Choose one of your teams from the sidebar."
                  : "Create a team or join one using an invite token."}
              </p>
            </section>
          )}
          {selected && !roster?.data && (
            <section
              className="dashboard-panel workspace-panel"
              aria-labelledby="team-error-heading"
            >
              <h2 id="team-error-heading">Team details unavailable</h2>
              <p>Refresh the workspace to check whether you can access this team.</p>
              <a className="text-link" href={teamUrl(selected.id)}>
                Retry team
              </a>
            </section>
          )}
          {selected && roster?.data && (
            <section
              className="workspace-team"
              aria-labelledby="selected-team-heading"
              key={selected.id}
            >
              <div className="workspace-team-heading">
                <h2 id="selected-team-heading">{selected.name}</h2>
                <span className="workspace-role">{roleName[selected.role]}</span>
              </div>
              <DocumentsPanel
                teamId={selected.id}
                canManage={selected.role !== "member"}
                initialDocuments={documents?.data?.documents ?? null}
              />
              <WebsitesPanel
                teamId={selected.id}
                canManage={selected.role !== "member"}
                initialWebsites={websites?.data?.websites ?? null}
              />
              {selected.role !== "member" && (
                <InviteForm teamId={selected.id} canInviteAdmins={selected.role === "owner"} />
              )}
              <section
                className="dashboard-panel workspace-panel"
                aria-labelledby="members-heading"
              >
                <h3 id="members-heading">Team members</h3>
                <ul className="workspace-roster">
                  {roster.data.members.map((member) => {
                    const manageable =
                      member.role !== "owner" &&
                      (selected.role === "owner" ||
                        (selected.role === "admin" && member.role === "member"));
                    return (
                      <li key={member.id} className="workspace-member">
                        <div>
                          <strong>{member.email}</strong>
                          <span className="workspace-role">{roleName[member.role]}</span>
                        </div>
                        {manageable && (
                          <MembershipActions
                            teamId={selected.id}
                            member={member}
                            canChangeRole={selected.role === "owner"}
                          />
                        )}
                      </li>
                    );
                  })}
                </ul>
              </section>
            </section>
          )}
        </main>
      </div>
    </div>
  );
}
