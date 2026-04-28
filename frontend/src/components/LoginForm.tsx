"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

import { probeBackend } from "@/lib/api";
import {
  BackendOption,
  buildBackendOptions,
  fetchLocalTailnetSnapshot,
  TailnetSnapshot,
} from "@/lib/tailnet";

interface LoginFormProps {
  defaultHost: string;
  onSubmit: (host: string, password: string) => Promise<void>;
}

const CUSTOM_VALUE = "__custom__";

type ProbeStatus = "idle" | "checking" | "reachable" | "unreachable";

export function LoginForm({ defaultHost, onSubmit }: LoginFormProps) {
  const [snapshot, setSnapshot] = useState<TailnetSnapshot | null>(null);
  const [snapshotLoading, setSnapshotLoading] = useState(true);
  const [selection, setSelection] = useState<string>("");
  const [customHost, setCustomHost] = useState(defaultHost);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [probe, setProbe] = useState<ProbeStatus>("idle");
  const probeAbortRef = useRef<AbortController | null>(null);
  const initializedRef = useRef(false);

  const pageHost = typeof window === "undefined" ? "localhost" : window.location.hostname || "localhost";

  const options = useMemo<BackendOption[]>(
    () => buildBackendOptions(pageHost, snapshot),
    [pageHost, snapshot],
  );

  const loadSnapshot = async (signal?: AbortSignal) => {
    setSnapshotLoading(true);
    try {
      const fetched = await fetchLocalTailnetSnapshot(signal);
      setSnapshot(fetched);
    } catch {
      setSnapshot({ available: false, error: "discovery failed", peers: [] });
    } finally {
      setSnapshotLoading(false);
    }
  };

  useEffect(() => {
    const controller = new AbortController();
    void loadSnapshot(controller.signal);
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (initializedRef.current || snapshotLoading) {
      return;
    }
    initializedRef.current = true;
    if (defaultHost) {
      const matched = options.find((option) => option.url === defaultHost);
      if (matched) {
        setSelection(matched.url);
        return;
      }
      setSelection(CUSTOM_VALUE);
      setCustomHost(defaultHost);
      return;
    }
    if (options.length > 0) {
      setSelection(options[0].url);
    }
  }, [snapshotLoading, options, defaultHost]);

  const activeHost = selection === CUSTOM_VALUE ? customHost.trim() : selection;

  useEffect(() => {
    probeAbortRef.current?.abort();
    if (!activeHost) {
      setProbe("idle");
      return;
    }
    const controller = new AbortController();
    probeAbortRef.current = controller;
    setProbe("checking");
    probeBackend(activeHost, controller.signal)
      .then((ok) => {
        if (!controller.signal.aborted) {
          setProbe(ok ? "reachable" : "unreachable");
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setProbe("unreachable");
        }
      });
    return () => controller.abort();
  }, [activeHost]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!activeHost) {
      setError("backend URL is required");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await onSubmit(activeHost, password);
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="panel stack" onSubmit={handleSubmit}>
      <div>
        <h2>Connect to Waypoint</h2>
        <p className="muted">
          {snapshot?.available
            ? "Pick a Tailscale peer or enter a custom URL."
            : "Tailscale not detected on this device. Enter the backend URL manually."}
        </p>
      </div>
      <label className="field">
        <span className="field-row">
          Backend
          <button
            type="button"
            className="link-button"
            onClick={() => void loadSnapshot()}
            disabled={snapshotLoading}
          >
            {snapshotLoading ? "Refreshing…" : "Refresh"}
          </button>
        </span>
        <select value={selection} onChange={(event) => setSelection(event.target.value)}>
          {options.map((option) => (
            <option key={option.url} value={option.url}>
              {option.label}
              {option.hint ? ` — ${option.hint}` : ""}
            </option>
          ))}
          <option value={CUSTOM_VALUE}>Custom URL…</option>
        </select>
      </label>
      {selection === CUSTOM_VALUE ? (
        <label className="field">
          <span>Custom backend URL</span>
          <input
            value={customHost}
            onChange={(event) => setCustomHost(event.target.value)}
            placeholder="http://100.x.y.z:8787"
          />
        </label>
      ) : null}
      {activeHost ? <ProbeIndicator status={probe} url={activeHost} /> : null}
      <label className="field">
        <span>Password</span>
        <input
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          placeholder="Waypoint password"
        />
      </label>
      {error ? <p className="error">{error}</p> : null}
      <button className="primary" disabled={busy || !activeHost} type="submit">
        {busy ? "Connecting…" : "Connect"}
      </button>
    </form>
  );
}

function ProbeIndicator({ status, url }: { status: ProbeStatus; url: string }) {
  if (status === "idle") {
    return null;
  }
  const text =
    status === "checking"
      ? `Checking ${url}…`
      : status === "reachable"
        ? `Reachable at ${url}`
        : `${url} unreachable`;
  return <p className={`probe ${status}`}>{text}</p>;
}
