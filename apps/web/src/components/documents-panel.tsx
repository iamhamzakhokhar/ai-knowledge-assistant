"use client";

import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  type DocumentDetail,
  type DocumentStatus,
  type DocumentSummary,
  documentUploadLimit,
} from "@/lib/workspace";
import { useWorkspaceAction } from "@/lib/workspace-client";

const statusName: Record<DocumentStatus, string> = {
  queued: "Queued",
  processing: "Processing",
  completed: "Completed",
  failed: "Failed",
};
const formatName = { pdf: "PDF", docx: "DOCX", markdown: "Markdown" };
const maximumAttempts = 3;
const failureMessage = new Map([
  ["queue_unavailable", "The processing queue was unavailable."],
  ["attempt_expired", "This processing attempt timed out."],
  ["authorization_revoked", "The uploader’s team permissions changed during processing."],
  ["encrypted_document", "Remove the file’s password protection before uploading."],
  ["empty_document", "The file contains no extractable text."],
  ["invalid_document", "This file is unreadable. Re-export it and upload it again."],
  ["scanned_pdf", "This PDF contains scanned pages without extractable text."],
  ["document_limits_exceeded", "The file exceeds the extraction limits."],
]);

class DocumentReadError extends Error {
  constructor(readonly status: number) {
    super("Document request failed");
  }
}

async function readDocumentData<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(`/api/workspace/${path}`, {
    credentials: "same-origin",
    cache: "no-store",
    signal,
  });
  if (!response.ok) throw new DocumentReadError(response.status);
  return (await response.json()) as T;
}

function documentFailure(document: DocumentSummary): string {
  return failureMessage.get(document.error ?? "") ?? "Text extraction failed for this file.";
}

function DocumentUpload({
  path,
  onSave,
}: {
  path: string;
  onSave: (document: DocumentSummary) => void;
}) {
  const { submit, pending, error } = useWorkspaceAction();
  const [validation, setValidation] = useState("");
  const [notice, setNotice] = useState("");

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const file = new FormData(form).get("document");
    setNotice("");
    if (!(file instanceof File) || file.size === 0) {
      setValidation("Choose a nonempty document.");
      return;
    }
    if (file.size > documentUploadLimit) {
      setValidation("Choose a file up to 5 MiB.");
      return;
    }
    if (!/\.(pdf|docx|md|markdown)$/i.test(file.name)) {
      setValidation("Choose a PDF, DOCX, or Markdown file.");
      return;
    }
    setValidation("");
    const result = await submit<DocumentSummary>(path, "POST", file, {
      413: "Choose a file up to 5 MiB.",
      415: "Choose a PDF, DOCX, or Markdown file.",
      422: "Check that the file is readable and matches its filename extension.",
    });
    if (result) {
      onSave(result.data);
      form.reset();
      setNotice(
        result.status === 200 ? "This document is already in the list." : "Document uploaded.",
      );
    }
  }

  return (
    <form
      className="workspace-form document-upload"
      method="post"
      onSubmit={upload}
      aria-busy={pending}
    >
      <div className="workspace-field">
        <label htmlFor="document-file">Document file</label>
        <input
          id="document-file"
          name="document"
          type="file"
          accept=".pdf,.docx,.md,.markdown"
          required
          aria-describedby="document-file-help"
          disabled={pending}
        />
        <small id="document-file-help">PDF, DOCX, or Markdown. Up to 5 MiB.</small>
      </div>
      <button className="button button-primary button-small" type="submit" disabled={pending}>
        {pending ? "Uploading…" : "Upload document"}
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

function DocumentRetry({
  path,
  document,
  onSave,
}: {
  path: string;
  document: DocumentSummary;
  onSave: (document: DocumentSummary) => void;
}) {
  const { submit, pending, error } = useWorkspaceAction();

  async function retry() {
    const result = await submit<DocumentSummary>(
      `${path}/${document.id}/retry`,
      "POST",
      {},
      {
        409: "Retry is unavailable for this document. Refresh the list to check its status.",
      },
    );
    if (result) onSave(result.data);
  }

  return (
    <div className="document-retry">
      <button
        className="button button-outline button-small"
        type="button"
        aria-label={`Retry ${document.name}`}
        disabled={pending}
        onClick={retry}
      >
        {pending ? "Retrying…" : "Retry"}
      </button>
      {error && (
        <p className="workspace-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

function DocumentPreview({
  path,
  document,
  close,
}: {
  path: string;
  document: DocumentSummary;
  close: () => void;
}) {
  const [detail, setDetail] = useState<DocumentDetail | null>(null);
  const [error, setError] = useState("");
  const endpoint = `${path}/${document.id}`;
  const title = useRef<HTMLHeadingElement>(null);
  const request = useRef<AbortController | null>(null);

  useEffect(() => title.current?.focus(), []);

  const fetchPreview = useCallback(async () => {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setError("");
    try {
      const data = await readDocumentData<DocumentDetail>(endpoint, controller.signal);
      if (!controller.signal.aborted) setDetail(data);
    } catch (cause) {
      if (controller.signal.aborted) return;
      if (cause instanceof DocumentReadError && cause.status === 401) {
        window.location.replace("/login");
        return;
      }
      setError("The extracted text is unavailable. Try again or refresh the document list.");
    } finally {
      if (request.current === controller) request.current = null;
    }
  }, [endpoint]);

  useEffect(() => {
    void fetchPreview();
    return () => request.current?.abort();
  }, [fetchPreview]);

  return (
    <section
      className="document-preview"
      id="document-preview"
      aria-labelledby="document-preview-title"
    >
      <div className="document-panel-heading">
        <div>
          <h4 id="document-preview-title" ref={title} tabIndex={-1}>
            Extracted text
          </h4>
          <p>{document.name}</p>
        </div>
        <button className="button button-outline button-small" type="button" onClick={close}>
          Close preview
        </button>
      </div>
      {!detail && !error && <p role="status">Loading extracted text…</p>}
      {error && (
        <div>
          <p className="workspace-error" role="alert">
            {error}
          </p>
          <button
            className="button button-outline button-small"
            type="button"
            onClick={() => void fetchPreview()}
          >
            Retry preview
          </button>
        </div>
      )}
      {detail?.sections.length === 0 && <p>No extracted text is available.</p>}
      {detail?.sections.map((section, index) => {
        const label = [section.page === null ? "" : `Page ${section.page}`, section.heading]
          .filter(Boolean)
          .join(" · ");
        return (
          // biome-ignore lint/suspicious/noArrayIndexKey: Sections are ordered snapshots of one extraction.
          <div className="document-preview-section" key={index}>
            {label && <p>{label}</p>}
            <pre className="document-preview-text">{section.text}</pre>
          </div>
        );
      })}
    </section>
  );
}

export function DocumentsPanel({
  teamId,
  canManage,
  initialDocuments,
}: {
  teamId: string;
  canManage: boolean;
  initialDocuments: DocumentSummary[] | null;
}) {
  const path = `teams/${teamId}/documents`;
  const [documents, setDocuments] = useState(initialDocuments);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(initialDocuments ? "" : "Documents could not be loaded.");
  const activeRefresh = useRef<AbortController | null>(null);
  const previewTrigger = useRef<HTMLButtonElement | null>(null);
  const preview = documents?.find(
    (document) => document.id === previewId && document.status === "completed",
  );
  const hasActiveDocuments = documents?.some(
    (document) => document.status === "queued" || document.status === "processing",
  );

  const refresh = useCallback(
    async (showProgress = false) => {
      if (activeRefresh.current) return;
      const controller = new AbortController();
      activeRefresh.current = controller;
      if (showProgress) setRefreshing(true);
      try {
        const result = await readDocumentData<{ documents: DocumentSummary[] }>(
          path,
          controller.signal,
        );
        if (!controller.signal.aborted) {
          setDocuments(result.documents);
          setError("");
        }
      } catch (cause) {
        if (controller.signal.aborted) return;
        if (cause instanceof DocumentReadError && cause.status === 401) {
          window.location.replace("/login");
          return;
        }
        if (cause instanceof DocumentReadError && (cause.status === 403 || cause.status === 404)) {
          setDocuments(null);
          setPreviewId(null);
          setError("This team’s documents are unavailable.");
        } else {
          setError("Documents could not be refreshed. Try again shortly.");
        }
      } finally {
        if (activeRefresh.current === controller) {
          activeRefresh.current = null;
          if (!controller.signal.aborted && showProgress) setRefreshing(false);
        }
      }
    },
    [path],
  );

  useEffect(() => () => activeRefresh.current?.abort(), []);

  useEffect(() => {
    if (!hasActiveDocuments) return;
    function refreshVisibleDocuments() {
      if (document.visibilityState === "visible") void refresh();
    }
    const timer = window.setInterval(refreshVisibleDocuments, 2000);
    document.addEventListener("visibilitychange", refreshVisibleDocuments);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshVisibleDocuments);
    };
  }, [hasActiveDocuments, refresh]);

  function saveDocument(document: DocumentSummary) {
    // A list request started before this mutation can return older state.
    activeRefresh.current?.abort();
    activeRefresh.current = null;
    setRefreshing(false);
    setDocuments((current) => [
      document,
      ...(current ?? []).filter((item) => item.id !== document.id),
    ]);
    setError("");
  }

  function closePreview() {
    setPreviewId(null);
    previewTrigger.current?.focus();
  }

  return (
    <section
      className="dashboard-panel workspace-panel documents-panel"
      aria-labelledby="documents-heading"
    >
      <div className="document-panel-heading">
        <h3 id="documents-heading">Documents</h3>
        <button
          className="button button-outline button-small"
          type="button"
          onClick={() => void refresh(true)}
          disabled={refreshing}
        >
          {refreshing ? "Refreshing…" : "Refresh documents"}
        </button>
      </div>
      {error && (
        <p className="workspace-error" role="alert">
          {error}
        </p>
      )}
      {canManage && documents !== null && <DocumentUpload path={path} onSave={saveDocument} />}
      {documents?.length === 0 && <p>No documents have been uploaded.</p>}
      {documents && documents.length > 0 && (
        <ul className="workspace-roster document-list" aria-label="Team documents">
          {documents.map((document) => (
            <li className="workspace-member document-row" key={document.id}>
              <div className="document-row-description">
                <strong>{document.name}</strong>
                <span>{formatName[document.format]}</span>
                <span
                  className={`document-status document-status-${document.status}`}
                  role="status"
                >
                  {statusName[document.status]}
                </span>
                {document.attempt > 1 && <span>Attempt {document.attempt}</span>}
                {document.status === "failed" && (
                  <p className="workspace-error">{documentFailure(document)}</p>
                )}
              </div>
              {document.status === "completed" && (
                <button
                  className="button button-outline button-small"
                  type="button"
                  aria-label={`Preview ${document.name}`}
                  aria-expanded={previewId === document.id}
                  aria-controls={previewId === document.id ? "document-preview" : undefined}
                  onClick={(event) => {
                    previewTrigger.current = event.currentTarget;
                    setPreviewId((current) => (current === document.id ? null : document.id));
                  }}
                >
                  Preview
                </button>
              )}
              {canManage &&
                document.status === "failed" &&
                (document.attempt < maximumAttempts ? (
                  <DocumentRetry path={path} document={document} onSave={saveDocument} />
                ) : (
                  <span className="document-notice">Retry limit reached.</span>
                ))}
            </li>
          ))}
        </ul>
      )}
      {preview && (
        <DocumentPreview
          key={`${preview.id}:${preview.updated_at}`}
          path={path}
          document={preview}
          close={closePreview}
        />
      )}
    </section>
  );
}
