"use client";

import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import type { WebsiteDetail, WebsitePage, WebsiteSummary } from "@/lib/workspace";
import { useWorkspaceAction } from "@/lib/workspace-client";
import {
  isProcessing,
  readWorkspaceData,
  statusName,
  useWorkspaceCollection,
  WorkspaceReadError,
} from "@/lib/workspace-collection";

const maximumAttempts = 3;
const urlError = "Enter a supported public HTTP or HTTPS URL.";
const pageStatus = { completed: "Crawled", skipped: "Skipped", failed: "Failed" };
const failureMessages = new Map([
  ["queue_unavailable", "The crawl queue was unavailable."],
  ["attempt_expired", "This crawl attempt timed out."],
  ["authorization_revoked", "The requester’s team permissions changed during the crawl."],
  ["no_pages_fetched", "No crawlable pages with extractable text were found."],
  ["robots_disallowed", "The site’s robots.txt rules disallow this page."],
  ["robots_unavailable", "The site’s robots.txt rules could not be read."],
  ["unsupported_content_type", "This page is not supported HTML."],
  ["invalid_sitemap", "The sitemap could not be read."],
  ["response_too_large", "The page exceeded the crawl size limit."],
  ["timeout", "The page took too long to respond."],
]);

function websiteLabel(website: WebsiteSummary): string {
  return website.sitemap ? `${website.url} (sitemap)` : website.url;
}

function isSupportedUrl(value: string): boolean {
  if (!/^https?:\/\//i.test(value)) return false;
  try {
    const url = new URL(value);
    return !url.username && !url.password;
  } catch {
    return false;
  }
}

function WebsiteForm({
  path,
  onSave,
}: {
  path: string;
  onSave: (website: WebsiteSummary) => void;
}) {
  const { submit, pending, error } = useWorkspaceAction();
  const [validation, setValidation] = useState("");
  const [notice, setNotice] = useState("");

  async function addWebsite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    const url = String(values.get("url") ?? "").trim();
    setNotice("");
    if (!isSupportedUrl(url)) {
      setValidation(urlError);
      return;
    }
    setValidation("");
    const result = await submit<WebsiteSummary>(
      path,
      "POST",
      { url, sitemap: values.has("sitemap") },
      { 422: urlError },
    );
    if (result) {
      onSave(result.data);
      form.reset();
      setNotice(result.status === 200 ? "This website is already in the list." : "Website added.");
    }
  }

  return (
    <form
      className="workspace-form document-upload"
      method="post"
      onSubmit={addWebsite}
      aria-busy={pending}
    >
      <div className="workspace-field">
        <label htmlFor="website-url">Website URL</label>
        <input
          id="website-url"
          name="url"
          type="url"
          maxLength={2048}
          placeholder="https://example.com/help"
          required
          disabled={pending}
          aria-invalid={validation ? true : undefined}
        />
      </div>
      <label className="website-sitemap-option">
        <input
          type="checkbox"
          name="sitemap"
          aria-describedby="website-sitemap-help"
          disabled={pending}
        />
        Use sitemap
      </label>
      <small className="website-sitemap-help" id="website-sitemap-help">
        Enter the sitemap URL when this option is selected.
      </small>
      <button className="button button-primary button-small" type="submit" disabled={pending}>
        {pending ? "Adding…" : "Add website"}
      </button>
      {(validation || error) && (
        <p className="workspace-error" role="alert">
          {validation || error}
        </p>
      )}
      {notice && (
        <p className="document-notice" role="status">
          {notice}
        </p>
      )}
    </form>
  );
}

function WebsiteRecrawl({
  path,
  website,
  onSave,
}: {
  path: string;
  website: WebsiteSummary;
  onSave: (website: WebsiteSummary) => void;
}) {
  const { submit, pending, error } = useWorkspaceAction();

  async function recrawl() {
    const result = await submit<WebsiteSummary>(
      `${path}/${website.id}/recrawl`,
      "POST",
      {},
      {
        409: "Recrawl is unavailable. Refresh the list to check the current attempt.",
      },
    );
    if (result) onSave(result.data);
  }

  return (
    <div className="document-retry">
      <button
        className="button button-outline button-small"
        type="button"
        aria-label={`Recrawl ${websiteLabel(website)}`}
        onClick={recrawl}
        disabled={pending}
      >
        {pending ? "Starting…" : "Recrawl"}
      </button>
      {error && (
        <p className="workspace-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

function PageOutcomes({ pages, heading }: { pages: WebsitePage[]; heading: string }) {
  const firstTextPage = pages.find((page) => page.text);
  return (
    <div className="website-outcomes">
      <h5>{heading}</h5>
      <ul className="workspace-roster">
        {pages.map((page, index) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: Ordered crawl snapshots may repeat a URL across page and sitemap outcomes.
          <li className="website-outcome" key={index}>
            {page.title && <strong>{page.title}</strong>}
            <p>{page.url}</p>
            {page.final_url && page.final_url !== page.url && <p>Final URL {page.final_url}</p>}
            <p className="website-page-status">{pageStatus[page.status]}</p>
            {page.error && (
              <p>{failureMessages.get(page.error) ?? "No text was collected from this page."}</p>
            )}
            {page.text && (
              <details open={page === firstTextPage}>
                <summary>Extracted text</summary>
                <pre className="document-preview-text">{page.text}</pre>
              </details>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function WebsitePreview({
  path,
  website,
  close,
}: {
  path: string;
  website: WebsiteSummary;
  close: () => void;
}) {
  const [result, setResult] = useState<{ detail: WebsiteDetail; version: string } | null>(null);
  const [error, setError] = useState("");
  const title = useRef<HTMLHeadingElement>(null);
  const request = useRef<AbortController | null>(null);
  const endpoint = `${path}/${website.id}`;
  const version = `${website.attempt}:${website.status}:${website.updated_at}`;
  const detail = result?.detail;

  useEffect(() => title.current?.focus(), []);

  const fetchPreview = useCallback(
    async (requestedVersion: string) => {
      request.current?.abort();
      const controller = new AbortController();
      request.current = controller;
      setError("");
      try {
        const data = await readWorkspaceData<WebsiteDetail>(endpoint, controller.signal);
        if (!controller.signal.aborted) setResult({ detail: data, version: requestedVersion });
      } catch (cause) {
        if (controller.signal.aborted) return;
        if (cause instanceof WorkspaceReadError && cause.status === 401) {
          window.location.replace("/login");
          return;
        }
        if (cause instanceof WorkspaceReadError && (cause.status === 403 || cause.status === 404)) {
          setResult(null);
        }
        setError("The crawl results are unavailable. Try again or refresh the website list.");
      } finally {
        if (request.current === controller) request.current = null;
      }
    },
    [endpoint],
  );

  useEffect(() => {
    void fetchPreview(version);
    return () => request.current?.abort();
  }, [fetchPreview, version]);

  return (
    <section
      className="document-preview"
      id="website-preview"
      aria-labelledby="website-preview-title"
    >
      <div className="document-panel-heading">
        <div>
          <h4 id="website-preview-title" ref={title} tabIndex={-1}>
            Crawl results
          </h4>
          <p>{websiteLabel(website)}</p>
        </div>
        <button className="button button-outline button-small" type="button" onClick={close}>
          Close website preview
        </button>
      </div>
      {!detail && !error && <p role="status">Loading crawl results…</p>}
      {error && (
        <div>
          <p className="workspace-error" role="alert">
            {error}
          </p>
          <button
            className="button button-outline button-small"
            type="button"
            onClick={() => void fetchPreview(version)}
          >
            Retry website preview
          </button>
        </div>
      )}
      {detail && (
        <>
          {detail.published_attempt === null && <p>No crawl results have been published.</p>}
          {detail.published_attempt !== null && <p>Published attempt {detail.published_attempt}</p>}
          {detail.pages.length > 0 && (
            <PageOutcomes heading="Published pages" pages={detail.pages} />
          )}
          {result.version === version && detail.last_attempt_pages.length > 0 && (
            <PageOutcomes heading="Last attempt outcomes" pages={detail.last_attempt_pages} />
          )}
        </>
      )}
    </section>
  );
}

export function WebsitesPanel({
  teamId,
  canManage,
  initialWebsites,
}: {
  teamId: string;
  canManage: boolean;
  initialWebsites: WebsiteSummary[] | null;
}) {
  const path = `teams/${teamId}/websites`;
  const [previewId, setPreviewId] = useState<string | null>(null);
  const previewTrigger = useRef<HTMLButtonElement | null>(null);
  const clearPreview = useCallback(() => setPreviewId(null), []);
  const {
    items: websites,
    error,
    refreshing,
    refresh,
    saveItem,
  } = useWorkspaceCollection(path, "websites", initialWebsites, clearPreview);
  const preview = websites?.find((website) => website.id === previewId);

  function closePreview() {
    clearPreview();
    previewTrigger.current?.focus();
  }

  return (
    <section
      className="dashboard-panel workspace-panel websites-panel"
      aria-labelledby="websites-heading"
    >
      <div className="document-panel-heading">
        <h3 id="websites-heading">Websites</h3>
        <button
          className="button button-outline button-small"
          type="button"
          disabled={refreshing}
          onClick={() => void refresh(true)}
        >
          {refreshing ? "Refreshing…" : "Refresh websites"}
        </button>
      </div>
      {error && (
        <p className="workspace-error" role="alert">
          {error}
        </p>
      )}
      {canManage && websites !== null && <WebsiteForm path={path} onSave={saveItem} />}
      {websites?.length === 0 && <p>No websites have been added.</p>}
      {websites && websites.length > 0 && (
        <ul className="workspace-roster" aria-label="Team websites">
          {websites.map((website) => (
            <li className="workspace-member document-row" key={website.id}>
              <div className="document-row-description">
                <strong>{website.url}</strong>
                <span className={`document-status document-status-${website.status}`} role="status">
                  {statusName[website.status]}
                </span>
                {website.sitemap && <span>Sitemap</span>}
                <span>
                  {website.processed} {website.processed === 1 ? "page" : "pages"} processed
                </span>
                {website.attempt > 1 && <span>Attempt {website.attempt}</span>}
                {website.status === "failed" && (
                  <p className="workspace-error">
                    {failureMessages.get(website.error ?? "") ??
                      "This website could not be crawled."}
                  </p>
                )}
              </div>
              <button
                className="button button-outline button-small"
                type="button"
                aria-label={`Preview ${websiteLabel(website)}`}
                aria-expanded={previewId === website.id}
                aria-controls={previewId === website.id ? "website-preview" : undefined}
                onClick={(event) => {
                  previewTrigger.current = event.currentTarget;
                  setPreviewId((current) => (current === website.id ? null : website.id));
                }}
              >
                Preview
              </button>
              {canManage &&
                !isProcessing(website.status) &&
                (website.attempt < maximumAttempts ? (
                  <WebsiteRecrawl path={path} website={website} onSave={saveItem} />
                ) : (
                  <span className="document-notice">Recrawl limit reached.</span>
                ))}
            </li>
          ))}
        </ul>
      )}
      {preview && (
        <WebsitePreview key={preview.id} path={path} website={preview} close={closePreview} />
      )}
    </section>
  );
}
