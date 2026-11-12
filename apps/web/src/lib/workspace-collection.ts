"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { ProcessingStatus } from "@/lib/workspace";

export const statusName: Record<ProcessingStatus, string> = {
  queued: "Queued",
  processing: "Processing",
  completed: "Completed",
  failed: "Failed",
};

export function isProcessing(status: ProcessingStatus): boolean {
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

export function useWorkspaceCollection<T extends { id: string; status: ProcessingStatus }>(
  path: string,
  collection: "documents" | "websites",
  initialItems: T[] | null,
  onAccessLost: () => void,
) {
  const label = collection === "documents" ? "Documents" : "Websites";
  const [items, setItems] = useState(initialItems);
  const [error, setError] = useState(initialItems ? "" : `${label} could not be loaded.`);
  const [refreshing, setRefreshing] = useState(false);
  const activeRefresh = useRef<AbortController | null>(null);
  const hasActiveItems = items?.some((item) => isProcessing(item.status));

  const refresh = useCallback(
    async (showProgress = false) => {
      if (activeRefresh.current) return;
      const controller = new AbortController();
      activeRefresh.current = controller;
      if (showProgress) setRefreshing(true);
      try {
        const result = await readWorkspaceData<Record<typeof collection, T[]>>(
          path,
          controller.signal,
        );
        if (!controller.signal.aborted) {
          setItems(result[collection]);
          setError("");
        }
      } catch (cause) {
        if (controller.signal.aborted) return;
        if (cause instanceof WorkspaceReadError && cause.status === 401) {
          window.location.replace("/login");
          return;
        }
        if (cause instanceof WorkspaceReadError && (cause.status === 403 || cause.status === 404)) {
          setItems(null);
          onAccessLost();
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
    [collection, label, onAccessLost, path],
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
    setItems((current) => [item, ...(current ?? []).filter((existing) => existing.id !== item.id)]);
    setError("");
  }

  return { items, error, refreshing, refresh, saveItem };
}
