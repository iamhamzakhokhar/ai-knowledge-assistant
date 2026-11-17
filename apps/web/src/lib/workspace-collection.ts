"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { IndexStatus, ProcessingStatus } from "@/lib/workspace";

export const statusName: Record<ProcessingStatus, string> = {
  queued: "Queued",
  processing: "Processing",
  completed: "Completed",
  failed: "Failed",
};

export function isProcessing(status: IndexStatus): boolean {
  return status === "queued" || status === "processing";
}

export class WorkspaceReadError extends Error {
  constructor(readonly status: number) {
    super("Workspace request failed");
  }
}

export async function readWorkspaceData<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(`/api/workspace/${path}`, {
    credentials: "same-origin",
    cache: "no-store",
    signal,
  });
  if (!response.ok) throw new WorkspaceReadError(response.status);
  return (await response.json()) as T;
}

export function useWorkspaceCollection<
  T extends { id: string; status: IndexStatus; kind?: "document" | "website" },
>(
  path: string,
  collection: "documents" | "websites" | "sources",
  initialItems: T[] | null,
  onAccessLost?: () => void,
  onChange?: () => void,
) {
  const label = { documents: "Documents", websites: "Websites", sources: "Indexed sources" }[
    collection
  ];
  const [items, setItems] = useState(initialItems);
  const [error, setError] = useState(initialItems ? "" : `${label} could not be loaded.`);
  const [refreshing, setRefreshing] = useState(false);
  const activeRefresh = useRef<AbortController | null>(null);
  const hasActiveItems = items?.some((item) => isProcessing(item.status));

  const refresh = useCallback(
    async (showProgress = false, restart = false) => {
      if (activeRefresh.current && !restart) return;
      activeRefresh.current?.abort();
      const controller = new AbortController();
      activeRefresh.current = controller;
      setRefreshing(showProgress);
      try {
        const result = await readWorkspaceData<Record<typeof collection, T[]>>(
          path,
          controller.signal,
        );
        if (!controller.signal.aborted) {
          setItems(result[collection]);
          setError("");
          onChange?.();
        }
      } catch (cause) {
        if (controller.signal.aborted) return;
        if (cause instanceof WorkspaceReadError && cause.status === 401) {
          window.location.replace("/login");
          return;
        }
        if (cause instanceof WorkspaceReadError && (cause.status === 403 || cause.status === 404)) {
          setItems(null);
          onAccessLost?.();
          setError(`This team’s ${collection} are unavailable.`);
        } else {
          setError(`${label} could not be refreshed. Try again shortly.`);
        }
      } finally {
        if (activeRefresh.current === controller) {
          activeRefresh.current = null;
          if (!controller.signal.aborted && showProgress) setRefreshing(false);
        }
      }
    },
    [collection, label, onAccessLost, onChange, path],
  );

  useEffect(
    () => () => {
      activeRefresh.current?.abort();
      activeRefresh.current = null;
    },
    [],
  );

  useEffect(() => {
    if (!hasActiveItems) return;
    function refreshVisibleItems() {
      if (document.visibilityState === "visible") void refresh();
    }
    const timer = window.setInterval(refreshVisibleItems, 2000);
    document.addEventListener("visibilitychange", refreshVisibleItems);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshVisibleItems);
    };
  }, [hasActiveItems, refresh]);

  function saveItem(item: T) {
    // A list request started before this mutation can return older state.
    activeRefresh.current?.abort();
    activeRefresh.current = null;
    setRefreshing(false);
    setItems((current) => [
      item,
      ...(current ?? []).filter(
        (existing) => existing.id !== item.id || existing.kind !== item.kind,
      ),
    ]);
    setError("");
    onChange?.();
  }

  return { items, error, refreshing, refresh, saveItem };
}
