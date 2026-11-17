"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { DocumentsPanel } from "@/components/documents-panel";
import { WebsitesPanel } from "@/components/websites-panel";
import type { DocumentSummary, IndexSource, IndexStatus, WebsiteSummary } from "@/lib/workspace";
import { useWorkspaceAction } from "@/lib/workspace-client";
import { isProcessing, statusName, useWorkspaceCollection } from "@/lib/workspace-collection";

const indexStatusName: Record<IndexStatus, string> = {
  ...statusName,
  completed: "Indexed",
  unindexed: "Unindexed",
  removed: "Removed",
};

function IndexActions({
  path,
  source,
  onSave,
}: {
  path: string;
  source: IndexSource;
  onSave: (source: IndexSource) => void;
}) {
  const { submit, pending, error } = useWorkspaceAction();
  const [confirming, setConfirming] = useState(false);
  const removeButton = useRef<HTMLButtonElement>(null);
  const cancelButton = useRef<HTMLButtonElement>(null);
  const sourcePath = `${path}/${source.kind}/${source.id}`;

  useEffect(() => {
    if (confirming) cancelButton.current?.focus();
  }, [confirming]);

  async function reindex() {
    const result = await submit<IndexSource>(
      `${sourcePath}/reindex`,
      "POST",
      {},
      {
        409: "This source can’t be reindexed right now. Check whether extraction or indexing is still running.",
      },
    );
    if (result) onSave(result.data);
  }

  async function remove() {
    if (await submit<void>(sourcePath, "DELETE")) {
      onSave({
        ...source,
        status: "removed",
        processed: 0,
        total: 0,
        error: null,
        active_version_id: null,
      });
    }
  }

  return (
    <div className="knowledge-index-actions" aria-busy={pending}>
      <div className="workspace-member-actions">
        <button
          type="button"
          className="button button-outline button-small"
          aria-label={`Reindex ${source.name}`}
          disabled={pending || confirming || isProcessing(source.status)}
          onClick={reindex}
        >
          Reindex
        </button>
        <button
          type="button"
          className="button button-outline button-small workspace-remove"
          ref={removeButton}
          aria-label={`Remove ${source.name}`}
          aria-expanded={confirming}
          disabled={pending}
          onClick={() => setConfirming((current) => !current)}
        >
          Remove
        </button>
      </div>
      {confirming && (
        <div className="knowledge-index-confirm">
          <p>Permanently remove this source from the index?</p>
          <div className="workspace-member-actions">
            <button
              type="button"
              className="button button-outline button-small"
              ref={cancelButton}
              disabled={pending}
              onClick={() => {
                setConfirming(false);
                removeButton.current?.focus();
              }}
            >
              Cancel removal
            </button>
            <button
              type="button"
              className="button button-outline button-small workspace-remove"
              aria-label={`Confirm remove ${source.name}`}
              disabled={pending}
              onClick={remove}
            >
              {pending ? "Removing…" : "Confirm remove"}
            </button>
          </div>
        </div>
      )}
      {error && (
        <p className="workspace-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

function KnowledgeIndex({
  teamId,
  canManage,
  initialSources,
  sourceRevision,
}: {
  teamId: string;
  canManage: boolean;
  initialSources: IndexSource[] | null;
  sourceRevision: number;
}) {
  const path = `teams/${teamId}/index`;
  const heading = useRef<HTMLHeadingElement>(null);
  const {
    items: sources,
    error,
    refreshing,
    refresh,
    saveItem,
  } = useWorkspaceCollection(path, "sources", initialSources);

  useEffect(() => {
    if (sourceRevision > 0) void refresh(false, true);
  }, [sourceRevision, refresh]);

  function saveSource(source: IndexSource) {
    saveItem(source);
    if (source.status === "removed") heading.current?.focus();
  }

  return (
    <section
      className="dashboard-panel workspace-panel knowledge-index"
      aria-labelledby="index-heading"
    >
      <div className="document-panel-heading">
        <h3 id="index-heading" ref={heading} tabIndex={-1}>
          Knowledge index
        </h3>
        <button
          className="button button-outline button-small"
          type="button"
          disabled={refreshing}
          onClick={() => void refresh(true)}
        >
          {refreshing ? "Refreshing…" : "Refresh index"}
        </button>
      </div>
      {error && (
        <p className="workspace-error" role="alert">
          {error}
        </p>
      )}
      {sources?.length === 0 && <p>Upload a document or add a website to get started.</p>}
      {sources && sources.length > 0 && (
        <ul className="workspace-roster" aria-label="Indexed sources">
          {sources.map((source) => (
            <li className="workspace-member" key={`${source.kind}:${source.id}`}>
              <div className="document-row-description">
                <strong>{source.name}</strong>
                <span>{source.kind === "document" ? "Document" : "Website"}</span>
                <span className={`document-status document-status-${source.status}`} role="status">
                  {indexStatusName[source.status]}
                </span>
                {isProcessing(source.status) && source.total > 0 && (
                  <span>
                    {source.processed} of {source.total} chunks
                  </span>
                )}
                <p>
                  {source.active_version_id ? (
                    <>
                      Active version <code>{source.active_version_id}</code>
                    </>
                  ) : (
                    "No active version"
                  )}
                </p>
                {source.status === "failed" && (
                  <p className="workspace-error">Indexing could not be completed.</p>
                )}
              </div>
              {canManage && source.status !== "removed" && (
                <IndexActions path={path} source={source} onSave={saveSource} />
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function TeamSources({
  teamId,
  canManage,
  initialDocuments,
  initialWebsites,
  initialSources,
}: {
  teamId: string;
  canManage: boolean;
  initialDocuments: DocumentSummary[] | null;
  initialWebsites: WebsiteSummary[] | null;
  initialSources: IndexSource[] | null;
}) {
  const [sourceRevision, setSourceRevision] = useState(0);
  const noteSourceChange = useCallback(() => setSourceRevision((previous) => previous + 1), []);

  return (
    <>
      <DocumentsPanel
        teamId={teamId}
        canManage={canManage}
        initialDocuments={initialDocuments}
        onChange={noteSourceChange}
      />
      <WebsitesPanel
        teamId={teamId}
        canManage={canManage}
        initialWebsites={initialWebsites}
        onChange={noteSourceChange}
      />
      <KnowledgeIndex
        teamId={teamId}
        canManage={canManage}
        initialSources={initialSources}
        sourceRevision={sourceRevision}
      />
    </>
  );
}
