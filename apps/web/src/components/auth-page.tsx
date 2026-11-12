import Link from "next/link";
import { AuthForm } from "@/components/auth-form";
import { Brand } from "@/components/brand";

export function AuthPage({ mode }: { mode: "login" | "signup" }) {
  const signup = mode === "signup";
  return (
    <div className="auth-layout">
      <header className="auth-header page-width">
        <Brand />
        <Link href="/project" className="text-link">
          Project overview
        </Link>
      </header>
      <main className="auth-main page-width" id="main-content">
        <div className="auth-card">
          <h1>{signup ? "Create an account" : "Sign in"}</h1>
          <p>
            {signup
              ? "Create a team or join one with an invite."
              : "Sign in to your KnowledgeDesk workspace."}
          </p>
          <AuthForm mode={mode} />
          <p className="auth-alternate">
            {signup ? "Already have an account?" : "New to KnowledgeDesk?"}{" "}
            <Link href={signup ? "/login" : "/signup"} className="text-link">
              {signup ? "Sign in" : "Create an account"}
            </Link>
          </p>
        </div>
      </main>
    </div>
  );
}
