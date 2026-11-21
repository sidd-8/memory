"use client";

import { useEffect, useState } from "react";

export const categories = [
  "semantic",
  "episodic",
  "procedural",
  "project",
  "working",
  "commitment",
];
export const states = [
  "tentative",
  "confirmed",
  "cancelled",
  "superseded",
  "completed",
];
export const scopes = [
  "subject:read",
  "memory:read",
  "memory:write",
  "memory:verify",
  "memory:delete",
  "graph:read",
];
export const can = (session, scope) => session?.scopes.includes(scope);
export const label = (value) =>
  value
    ? value.replaceAll("_", " ").replace(/^./, (first) => first.toUpperCase())
    : "—";
export const shortId = (value) => value?.slice(0, 8);
export const timestamp = (value, timeZone) =>
  value
    ? new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
        timeZone,
      }).format(new Date(value))
    : "—";
export const tone = (status) =>
  ({
    active: "green",
    confirmed: "green",
    review_due: "amber",
    stale: "amber",
    tentative: "amber",
    candidate: "indigo",
  })[status] || "neutral";

export async function api(
  session,
  path,
  { method = "GET", data, signal } = {},
) {
  const url = new URL("/api/v1/" + path, window.location.origin);
  url.searchParams.set("workspace_id", session.workspaceId);
  url.searchParams.set("subject_id", session.subjectId);
  const headers = { Authorization: "Bearer " + session.key };
  if (data !== undefined) {
    headers["Content-Type"] = "application/json";
    headers["Idempotency-Key"] = crypto.randomUUID();
  }
  const response = await fetch(url, {
    method,
    headers,
    signal,
    cache: "no-store",
    body: data === undefined ? undefined : JSON.stringify(data),
  });
  if (response.status === 204) return null;
  const result = await response.json();
  if (!response.ok) {
    const detail = Array.isArray(result.detail)
      ? result.detail[0]?.msg
      : result.detail;
    throw new Error(detail || "The request failed.");
  }
  return result;
}

export function useResource(session, path, revision = 0) {
  const [result, setResult] = useState(null);
  useEffect(() => {
    if (!session || !path) return;
    const controller = new AbortController();
    api(session, path, { signal: controller.signal }).then(
      (data) => {
        if (!controller.signal.aborted)
          setResult({ session, path, revision, data });
      },
      (error) => {
        if (!controller.signal.aborted)
          setResult({ session, path, revision, error: error.message });
      },
    );
    return () => controller.abort();
  }, [session, path, revision]);
  return result?.session === session &&
    result.path === path &&
    result.revision === revision
    ? result
    : { loading: Boolean(session && path) };
}

export function useAction() {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  async function run(action) {
    setError("");
    setPending(true);
    try {
      await action();
    } catch (failure) {
      setError(failure.message);
    } finally {
      setPending(false);
    }
  }
  return { run, pending, error };
}
