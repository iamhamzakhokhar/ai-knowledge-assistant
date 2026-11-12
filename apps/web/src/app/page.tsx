import Link from "next/link";
import { Brand } from "@/components/brand";
import { Icon, type IconName } from "@/components/icons";

const repository = "https://github.com/iamhamzakhokhar/ai-knowledge-assistant";

const features: { icon: IconName; eyebrow: string; title: string; text: string }[] = [
  {
    icon: "layers",
    eyebrow: "01 / CONNECT",
    title: "One place for what your team knows.",
    text: "The roadmap brings documents and website content into a searchable, team-specific knowledge base.",
  },
  {
    icon: "link",
    eyebrow: "02 / UNDERSTAND",
    title: "Answers with a way to check them.",
    text: "Planned hybrid retrieval pairs relevant passages with citations, so an answer can lead back to its source.",
  },
  {
    icon: "shield",
    eyebrow: "03 / STAY IN CONTROL",
    title: "A helpful assistant. A human decision.",
    text: "The planned action flow drafts support tickets, leads, and handoffs for a person to review before anything changes.",
  },
];

export default function HomePage() {
  return (
    <div className="marketing-page">
      <header className="site-header page-width">
        <Brand />
        <nav aria-label="Main navigation" className="site-nav">
          <a href="#features">The idea</a>
          <a href="#how-it-works">How it works</a>
          <a href={repository}>
            GitHub <Icon name="arrowUp" />
          </a>
        </nav>
        <Link href="/project" className="button button-small button-dark header-cta">
          Explore project <Icon name="arrow" />
        </Link>
      </header>

      <main id="main-content">
        <section className="hero page-width" aria-labelledby="hero-title">
          <div className="hero-copy">
            <a className="announcement" href={`${repository}#readme`}>
              <span className="status-dot" /> Built in the open{" "}
              <span className="announcement-divider" /> Portfolio project <Icon name="arrowUp" />
            </a>
            <h1 id="hero-title">
              Your knowledge.
              <br />A <span>clearer</span> answer.
            </h1>
            <p className="hero-description">
              A home for your team’s knowledge.
              <br className="desktop-break" /> An assistant designed to make sense of it.
            </p>
            <p className="hero-detail">
              KnowledgeDesk is an open-source project exploring cited AI answers, connected
              documents, and actions that keep people in control.
            </p>
            <div className="hero-actions">
              <Link href="/dashboard" prefetch={false} className="button button-primary">
                Open dashboard <Icon name="arrow" />
              </Link>
              <a href={repository} className="button button-outline">
                <Icon name="code" /> View the source
              </a>
            </div>
            <div className="hero-note">
              <span className="tiny-check">
                <Icon name="check" />
              </span>{" "}
              MIT licensed <span className="dot-separator">·</span> Team workspaces{" "}
              <span className="dot-separator">·</span> No API keys needed to explore
            </div>
          </div>

          <figure
            className="hero-visual"
            aria-label="Illustrative chat preview using fictional example content"
          >
            <div className="visual-grid" />
            <div className="source-float source-float-top">
              <span className="file-icon">
                <Icon name="document" />
              </span>
              <div>
                <strong>team-handbook.pdf</strong>
                <span>Example knowledge</span>
              </div>
              <span className="source-check">
                <Icon name="check" />
              </span>
            </div>
            <div className="chat-preview">
              <div className="preview-header">
                <div className="assistant-symbol">
                  <Icon name="sparkle" />
                </div>
                <div>
                  <strong>Your knowledge assistant</strong>
                  <span>Illustrative preview</span>
                </div>
                <span className="preview-label">SAMPLE</span>
              </div>
              <div className="preview-content">
                <div className="preview-question">What happens after a support request?</div>
                <div className="preview-answer-heading">
                  <span className="small-sparkle">
                    <Icon name="sparkle" />
                  </span>{" "}
                  From your knowledge
                </div>
                <p>
                  In this example, requests enter a triage queue. A teammate reviews the details and
                  assigns an owner.
                </p>
                <p>The team can then track the request and share an update with the customer.</p>
                <a className="citation" href="#preview-note">
                  <Icon name="document" />
                  <span>
                    Example support guide <span>· p. 4</span>
                  </span>
                  <Icon name="arrowUp" />
                </a>
                <div className="preview-divider" />
                <div className="preview-footer">
                  <Icon name="shield" />
                  <span>Source-led answers. Human-approved actions.</span>
                </div>
              </div>
            </div>
            <div className="source-float source-float-bottom">
              <span className="web-icon">
                <Icon name="globe" />
              </span>
              <div>
                <strong>Knowledge, connected.</strong>
                <span>Documents + websites, on the roadmap</span>
              </div>
            </div>
          </figure>
        </section>

        <div className="platform-strip page-width">
          <p>
            One lightweight widget.<span> Designed for the web you already use.</span>
          </p>
          <ul aria-label="Platforms targeted by the planned embed guides">
            <li>WordPress</li>
            <li>Shopify</li>
            <li>Webflow</li>
            <li>HubSpot</li>
            <li>Drupal</li>
            <li>Magento</li>
          </ul>
          <span className="platform-note">Platform install guides are planned.</span>
        </div>

        <section
          className="features-section page-width"
          id="features"
          aria-labelledby="features-title"
        >
          <div className="section-heading">
            <div>
              <span className="eyebrow">LESS SEARCHING. MORE UNDERSTANDING.</span>
              <h2 id="features-title">
                Good answers start
                <br />
                with your knowledge.
              </h2>
            </div>
            <p>
              A practical AI project, built around three simple ideas. Follow the implementation as
              each feature becomes real.
            </p>
          </div>
          <div className="feature-grid">
            {features.map((feature) => (
              <article className="feature-card" key={feature.eyebrow}>
                <span className="feature-icon">
                  <Icon name={feature.icon} />
                </span>
                <span className="feature-eyebrow">{feature.eyebrow}</span>
                <h3>{feature.title}</h3>
                <p>{feature.text}</p>
                <span className="planned-label">Planned capability</span>
              </article>
            ))}
          </div>
        </section>

        <section className="workflow-section" id="how-it-works" aria-labelledby="workflow-title">
          <div className="page-width workflow-inner">
            <div>
              <span className="eyebrow">FROM SOURCE TO ANSWER</span>
              <h2 id="workflow-title">
                A clear path.
                <br />
                At every step.
              </h2>
              <p>
                The intended flow is easy to follow, inspect, and explain. This first milestone
                establishes the foundation.
              </p>
              <Link href="/project#roadmap" className="text-link">
                See the project roadmap <Icon name="arrow" />
              </Link>
            </div>
            <ol className="workflow-steps">
              <li>
                <span>01</span>
                <div>
                  <h3>Connect the knowledge</h3>
                  <p>Add approved documents or website pages to a team’s workspace.</p>
                </div>
              </li>
              <li>
                <span>02</span>
                <div>
                  <h3>Ask. Find. Cite.</h3>
                  <p>
                    Retrieve relevant passages and generate an answer grounded in those sources.
                  </p>
                </div>
              </li>
              <li>
                <span>03</span>
                <div>
                  <h3>Review the next step</h3>
                  <p>A person approves a proposed action before the assistant carries it out.</p>
                </div>
              </li>
            </ol>
          </div>
        </section>

        <section className="build-banner page-width" aria-labelledby="build-title">
          <div>
            <span className="eyebrow">A PROJECT YOU CAN LOOK INSIDE</span>
            <h2 id="build-title">Built to be understood.</h2>
            <p>Readable code. Honest progress. An open roadmap.</p>
          </div>
          <a href={repository} className="button button-primary">
            Follow the build on GitHub <Icon name="arrowUp" />
          </a>
        </section>
        <p id="preview-note" className="preview-disclaimer page-width">
          The answer preview uses fictional sample content. Working chat, knowledge ingestion,
          platform installs, and agent actions remain on the roadmap.
        </p>
      </main>

      <footer className="site-footer page-width">
        <Brand />
        <p>Built by Hamza Zahid Khokhar.</p>
        <div>
          <a href={`${repository}/blob/main/LICENSE`}>MIT License</a>
          <a href={repository}>
            GitHub <Icon name="arrowUp" />
          </a>
        </div>
      </footer>
    </div>
  );
}
