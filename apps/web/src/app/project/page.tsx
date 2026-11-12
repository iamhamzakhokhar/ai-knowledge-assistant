import type { Metadata } from "next";
import Link from "next/link";
import { Brand } from "@/components/brand";
import { Icon, type IconName } from "@/components/icons";

export const metadata: Metadata = { title: "Project overview" };

const repository = "https://github.com/iamhamzakhokhar/ai-knowledge-assistant";
const roadmap: { phase: string; title: string; description: string; icon: IconName }[] = [
  {
    phase: "01",
    title: "Setup",
    description:
      "Establish the monorepo, application shells, local services, and continuous integration.",
    icon: "grid",
  },
  {
    phase: "02",
    title: "Core features",
    description:
      "Add team isolation, ingestion, retrieval, citations, widget chat, and approved actions.",
    icon: "layers",
  },
  {
    phase: "03",
    title: "Quality",
    description:
      "Verify guardrails, evaluations, accessibility, performance, and failure recovery.",
    icon: "shield",
  },
  {
    phase: "04",
    title: "Release",
    description: "Publish deployment guides, measured results, a safe live demo, and v1.0.0.",
    icon: "globe",
  },
];

export default function ProjectPage() {
  return (
    <div className="dashboard-layout">
      <aside className="dashboard-sidebar">
        <Brand />
        <div className="workspace-label">
          <span className="workspace-avatar">K</span>
          <div>
            <strong>Project workspace</strong>
            <span>Public portfolio build</span>
          </div>
        </div>
        <span className="sidebar-label">WORKSPACE</span>
        <nav aria-label="Dashboard navigation" className="dashboard-nav">
          <Link href="/project" className="active" aria-current="page">
            <Icon name="grid" />
            Overview
          </Link>
          <a href="#setup">
            <Icon name="code" />
            Getting started
          </a>
          <a href="#roadmap">
            <Icon name="layers" />
            Roadmap
          </a>
          <a href={`${repository}#readme`}>
            <Icon name="book" />
            Documentation
            <Icon name="arrowUp" />
          </a>
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-build-note">
            <span className="status-dot" />
            <strong>Building the foundation</strong>
            <p>Follow each milestone as the assistant takes shape.</p>
            <a href={`${repository}/issues`}>
              View open issues <Icon name="arrowUp" />
            </a>
          </div>
          <Link href="/" className="back-link">
            Back to homepage <Icon name="arrow" />
          </Link>
        </div>
      </aside>

      <div className="dashboard-body">
        <header className="dashboard-topbar">
          <div className="breadcrumb">
            Workspace <span>/</span>
            <strong>Overview</strong>
          </div>
          <a href={repository} className="source-link">
            <Icon name="code" /> View source <Icon name="arrowUp" />
          </a>
        </header>
        <main className="dashboard-main" id="main-content">
          <div className="dashboard-page-heading">
            <div>
              <span className="eyebrow">YOUR KNOWLEDGE, TAKING SHAPE</span>
              <h1>Project overview</h1>
              <p>A look inside KnowledgeDesk, from its first commit onward.</p>
            </div>
            <span className="setup-status">
              <span className="status-dot" />
              Core features in progress
            </span>
          </div>

          <section className="dashboard-welcome" aria-labelledby="welcome-title">
            <div className="welcome-art" aria-hidden="true">
              <span />
              <span />
              <span />
              <Icon name="book" />
            </div>
            <div className="welcome-copy">
              <span className="eyebrow">THE FOUNDATION COMES FIRST</span>
              <h2 id="welcome-title">A workspace for a better answer.</h2>
              <p>
                Create a team or join one with an invitation. The roadmap covers connected
                knowledge, cited answers, and actions that require approval.
              </p>
              <Link
                href="/dashboard"
                prefetch={false}
                className="button button-primary button-small"
              >
                Open dashboard <Icon name="arrow" />
              </Link>
            </div>
          </section>

          <div className="dashboard-detail-grid">
            <section className="dashboard-panel" id="setup" aria-labelledby="setup-title">
              <div className="panel-heading">
                <h2 id="setup-title">Start with the project</h2>
                <span className="subtle-label">DEVELOPER GUIDE</span>
              </div>
              <p className="panel-intro">
                These links point to the source and its documented setup. No account or API key is
                required to browse.
              </p>
              <ol className="setup-list">
                <li>
                  <span className="step-number">1</span>
                  <div>
                    <h3>Read the overview</h3>
                    <p>Understand the architecture, scope, and current project status.</p>
                    <a href={`${repository}#readme`}>
                      Open the README <Icon name="arrowUp" />
                    </a>
                  </div>
                </li>
                <li>
                  <span className="step-number">2</span>
                  <div>
                    <h3>Run the local environment</h3>
                    <p>
                      Follow the repository’s getting-started instructions and environment examples.
                    </p>
                    <a href={`${repository}#getting-started`}>
                      View setup instructions <Icon name="arrowUp" />
                    </a>
                  </div>
                </li>
                <li>
                  <span className="step-number">3</span>
                  <div>
                    <h3>Pick up the next feature</h3>
                    <p>Review the milestones and acceptance criteria on GitHub.</p>
                    <a href={`${repository}/milestones`}>
                      Explore milestones <Icon name="arrowUp" />
                    </a>
                  </div>
                </li>
              </ol>
            </section>

            <section className="dashboard-panel current-scope" aria-labelledby="scope-title">
              <div className="panel-heading">
                <h2 id="scope-title">What’s available here</h2>
                <Icon name="sparkle" />
              </div>
              <div className="scope-item">
                <span className="scope-check">
                  <Icon name="check" />
                </span>
                <div>
                  <h3>Marketing page</h3>
                  <p>A responsive introduction to the project and its intended features.</p>
                </div>
              </div>
              <div className="scope-item">
                <span className="scope-check">
                  <Icon name="check" />
                </span>
                <div>
                  <h3>Team workspace</h3>
                  <p>Manage team membership, upload documents, and preview extracted text.</p>
                </div>
              </div>
              <div className="scope-notice">
                <Icon name="message" />
                <div>
                  <strong>The assistant is still being built.</strong>
                  <p>
                    Upcoming milestones cover website sources, live chat, billing, and analytics.
                  </p>
                </div>
              </div>
              <a href={`${repository}/issues`} className="text-link">
                See what’s coming next <Icon name="arrow" />
              </a>
            </section>
          </div>

          <section className="roadmap-section" id="roadmap" aria-labelledby="roadmap-title">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">ONE MILESTONE AT A TIME</span>
                <h2 id="roadmap-title">The road to v1.0</h2>
              </div>
              <a href={`${repository}/milestones`} className="text-link">
                GitHub milestones <Icon name="arrowUp" />
              </a>
            </div>
            <div className="roadmap-grid">
              {roadmap.map((item, index) => (
                <article
                  className={`roadmap-card ${index === 1 ? "current" : ""}`}
                  key={item.phase}
                >
                  <div className="roadmap-card-top">
                    <span className="roadmap-icon">
                      <Icon name={item.icon} />
                    </span>
                    <span>{item.phase}</span>
                  </div>
                  <h3>{item.title}</h3>
                  <p>{item.description}</p>
                  <span className={index === 1 ? "roadmap-status active" : "roadmap-status"}>
                    {index === 0 ? "Complete" : index === 1 ? "Current milestone" : "Planned"}
                  </span>
                </article>
              ))}
            </div>
          </section>

          <footer className="dashboard-footer">
            <p>
              Built by Hamza Zahid Khokhar <span>·</span> Open source under MIT
            </p>
            <a href={repository}>
              iamhamzakhokhar/ai-knowledge-assistant <Icon name="arrowUp" />
            </a>
          </footer>
        </main>
      </div>
    </div>
  );
}
