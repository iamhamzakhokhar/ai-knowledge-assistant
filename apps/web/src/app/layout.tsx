import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "KnowledgeDesk — Your knowledge, a clearer answer",
    template: "%s | KnowledgeDesk",
  },
  description:
    "An open-source AI knowledge assistant portfolio project. Explore the plan for cited answers, connected knowledge, and human-approved actions.",
  applicationName: "KnowledgeDesk",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <a className="skip-link" href="#main-content">
          Skip to content
        </a>
        {children}
      </body>
    </html>
  );
}
