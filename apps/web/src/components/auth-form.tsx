"use client";

import type { FormEvent } from "react";
import type { Session } from "@/lib/workspace";
import { useWorkspaceAction } from "@/lib/workspace-client";

export function AuthForm({ mode }: { mode: "login" | "signup" }) {
  const { submit, pending, error } = useWorkspaceAction(false);
  const signup = mode === "signup";

  async function authenticate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    const payload: Record<string, string> = {
      email: String(values.get("email") ?? "").trim(),
      password: String(values.get("password") ?? ""),
    };
    const team = String(values.get("team_name") ?? "").trim();
    if (signup && team) payload.team_name = team;
    const result = await submit<Session>(`auth/${mode}`, "POST", payload, {
      401: "Check your email and password.",
      409: "Your account could not be created. Sign in if you already have an account.",
    });
    if (result) {
      form.reset();
      window.location.replace("/dashboard");
    }
  }

  return (
    <form className="workspace-form" method="post" onSubmit={authenticate} aria-busy={pending}>
      <label className="workspace-field">
        <span>Email</span>
        <input name="email" type="email" autoComplete="email" required maxLength={254} />
      </label>
      <div className="workspace-field">
        <label htmlFor="password">Password</label>
        <input
          id="password"
          name="password"
          type="password"
          autoComplete={signup ? "new-password" : "current-password"}
          required
          minLength={signup ? 15 : undefined}
          maxLength={128}
          aria-describedby={signup ? "password-help" : undefined}
        />
        {signup && <small id="password-help">Use at least 15 characters.</small>}
      </div>
      {signup && (
        <div className="workspace-field">
          <label htmlFor="team-name">Team name</label>
          <input
            id="team-name"
            name="team_name"
            autoComplete="organization"
            maxLength={100}
            aria-describedby="team-name-help"
          />
          <small id="team-name-help">
            Optional. You can create or join a team after signing up.
          </small>
        </div>
      )}
      {error && (
        <p className="workspace-error" role="alert">
          {error}
        </p>
      )}
      <button className="button button-primary" type="submit" disabled={pending}>
        {pending
          ? signup
            ? "Creating account…"
            : "Signing in…"
          : signup
            ? "Create account"
            : "Sign in"}
      </button>
      <noscript>
        <p className="workspace-error">Enable JavaScript to submit this form.</p>
      </noscript>
    </form>
  );
}
