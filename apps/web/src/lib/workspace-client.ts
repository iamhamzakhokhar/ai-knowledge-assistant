"use client";

import { useRef, useState } from "react";

type Method = "POST" | "PATCH" | "DELETE";

export function useWorkspaceAction(requiresSession = true) {
  const running = useRef(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  async function submit<T>(
    path: string,
    method: Method,
    payload: Record<string, string> | File = {},
    messages: Partial<Record<number, string>> = {},
  ): Promise<{ data: T; status: number } | null> {
    if (running.current) return null;
    running.current = true;
    setPending(true);
    setError("");
    try {
      const upload = payload instanceof File;
      const response = await fetch(`/api/workspace/${path}`, {
        method,
        headers: upload
          ? {
              "Content-Type": "application/octet-stream",
              "X-Document-Name": encodeURIComponent(payload.name),
            }
          : { "Content-Type": "application/json" },
        credentials: "same-origin",
        cache: "no-store",
        body: upload ? payload : JSON.stringify(payload),
      });
      if (requiresSession && response.status === 401) {
        window.location.replace("/login");
        return null;
      }
      if (!response.ok) {
        const details = (await response.json().catch(() => null)) as { error?: string } | null;
        setError(messages[response.status] ?? details?.error ?? "The request failed. Try again.");
        return null;
      }
      return {
        data: (response.status === 204 ? undefined : await response.json()) as T,
        status: response.status,
      };
    } catch {
      setError("The workspace service could not be reached. Try again shortly.");
      return null;
    } finally {
      running.current = false;
      setPending(false);
    }
  }

  return { submit, pending, error };
}
