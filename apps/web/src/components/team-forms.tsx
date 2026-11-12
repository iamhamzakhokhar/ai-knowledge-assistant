"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";
import { type Invite, type Member, type Role, type Team, teamUrl } from "@/lib/workspace";
import { useWorkspaceAction } from "@/lib/workspace-client";

function FormError({ error }: { error: string }) {
  return error ? (
    <p className="workspace-error" role="alert">
      {error}
    </p>
  ) : null;
}

export function SignOutButton() {
  const { submit, pending, error } = useWorkspaceAction();

  async function signOut() {
    if (await submit<void>("auth/logout", "POST")) window.location.replace("/login");
  }

  return (
    <div className="workspace-sign-out">
      <button
        className="button button-outline button-small"
        type="button"
        disabled={pending}
        onClick={signOut}
      >
        {pending ? "Signing out…" : "Sign out"}
      </button>
      <FormError error={error} />
    </div>
  );
}

export function TeamForm({ mode }: { mode: "create" | "join" }) {
  const { submit, pending, error } = useWorkspaceAction();
  const joining = mode === "join";
  const title = joining ? "Join team" : "Create team";
  const inputName = joining ? "token" : "name";

  async function saveTeam(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    const result = await submit<Team>(
      joining ? "teams/join" : "teams",
      "POST",
      {
        [inputName]: String(values.get(inputName) ?? "").trim(),
      },
      joining
        ? { 404: "This invite is unavailable. Ask a team manager for a new invite." }
        : undefined,
    );
    if (result) window.location.assign(teamUrl(result.data.id));
  }

  return (
    <section className="dashboard-panel workspace-panel" aria-labelledby={`${mode}-team-heading`}>
      <h2 id={`${mode}-team-heading`}>{title}</h2>
      {joining && <p>Paste the token shared by your team manager.</p>}
      <form className="workspace-form" method="post" onSubmit={saveTeam} aria-busy={pending}>
        <label className="workspace-field">
          <span>{joining ? "Invite token" : "Team name"}</span>
          <input
            name={inputName}
            autoComplete={joining ? "off" : "organization"}
            autoCapitalize={joining ? "none" : undefined}
            spellCheck={joining ? false : undefined}
            required
            maxLength={joining ? 512 : 100}
          />
        </label>
        <FormError error={error} />
        <button className="button button-primary button-small" type="submit" disabled={pending}>
          {pending ? (joining ? "Joining team…" : "Creating team…") : title}
        </button>
      </form>
    </section>
  );
}

export function InviteForm({
  teamId,
  canInviteAdmins,
}: {
  teamId: string;
  canInviteAdmins: boolean;
}) {
  const { submit, pending, error } = useWorkspaceAction();
  const [invite, setInvite] = useState<Invite | null>(null);
  const [copyStatus, setCopyStatus] = useState("");

  async function createInvite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    setInvite(null);
    setCopyStatus("");
    const result = await submit<Invite>(`teams/${teamId}/invites`, "POST", {
      email: String(values.get("email") ?? "").trim(),
      role: String(values.get("role") ?? "member"),
    });
    if (result) {
      setInvite(result.data);
      form.reset();
    }
  }

  async function copyToken() {
    if (!invite) return;
    try {
      await navigator.clipboard.writeText(invite.token);
      setCopyStatus("Invite token copied.");
    } catch {
      setCopyStatus("Select the token to copy it.");
    }
  }

  return (
    <section className="dashboard-panel workspace-panel" aria-labelledby="invite-heading">
      <h2 id="invite-heading">Invite a teammate</h2>
      <p>Create a token to share with the invited person.</p>
      <form className="workspace-form" method="post" onSubmit={createInvite} aria-busy={pending}>
        <div className="workspace-form-row">
          <label className="workspace-field">
            <span>Invite email</span>
            <input name="email" type="email" required maxLength={254} />
          </label>
          <label className="workspace-field">
            <span>Invite role</span>
            <select name="role" defaultValue="member">
              <option value="member">Member</option>
              {canInviteAdmins && <option value="admin">Admin</option>}
            </select>
          </label>
        </div>
        <FormError error={error} />
        <button className="button button-primary button-small" type="submit" disabled={pending}>
          {pending ? "Creating invite…" : "Create invite"}
        </button>
      </form>
      {invite && (
        <div className="workspace-invite-result">
          <p role="status">Invite created for {invite.email}. Share this token privately.</p>
          <label className="workspace-field">
            <span>New invite token</span>
            <input value={invite.token} readOnly autoComplete="off" spellCheck={false} />
          </label>
          <button className="button button-outline button-small" type="button" onClick={copyToken}>
            Copy invite token
          </button>
          {copyStatus && <p role="status">{copyStatus}</p>}
        </div>
      )}
    </section>
  );
}

export function MembershipActions({
  teamId,
  member,
  canChangeRole,
}: {
  teamId: string;
  member: Member;
  canChangeRole: boolean;
}) {
  const router = useRouter();
  const { submit, pending, error } = useWorkspaceAction();
  const [role, setRole] = useState<Role>(member.role);
  const path = `teams/${teamId}/members/${member.id}`;

  async function updateRole(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (role === member.role) return;
    if (await submit<Member>(path, "PATCH", { role })) router.refresh();
  }

  async function removeMember() {
    if (!window.confirm(`Remove ${member.email} from this team?`)) return;
    if (await submit<void>(path, "DELETE")) router.refresh();
  }

  return (
    <div className="workspace-member-actions">
      {canChangeRole && (
        <form
          className="workspace-member-form"
          method="post"
          onSubmit={updateRole}
          aria-busy={pending}
        >
          <select
            aria-label={`Role for ${member.email}`}
            value={role}
            onChange={(event) => setRole(event.target.value as Role)}
            disabled={pending}
          >
            <option value="member">Member</option>
            <option value="admin">Admin</option>
          </select>
          <button
            className="button button-outline button-small"
            type="submit"
            aria-label={`Save role for ${member.email}`}
            disabled={pending || role === member.role}
          >
            Save role
          </button>
        </form>
      )}
      <button
        className="button button-outline button-small workspace-remove"
        type="button"
        aria-label={`Remove ${member.email}`}
        onClick={removeMember}
        disabled={pending}
      >
        Remove
      </button>
      <FormError error={error} />
    </div>
  );
}
