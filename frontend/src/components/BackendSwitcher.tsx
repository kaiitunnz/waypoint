"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { isAuthError, probeBackend } from "@/lib/api";
import {
  BackendOption,
  buildBackendOptions,
  fetchBackendTailnetSnapshot,
  TailnetSnapshot,
} from "@/lib/tailnet";

interface BackendSwitcherProps {
  host: string;
  token: string;
  onSwitch: (nextHost: string) => void;
  onAuthFailure?: () => void;
}

const CUSTOM_VALUE = "__custom__";
type ProbeStatus = "idle" | "checking" | "reachable" | "unreachable";

export function BackendSwitcher({ host, token, onSwitch, onAuthFailure }: BackendSwitcherProps) {
  const [open, setOpen] = useState(false);
  const [snapshot, setSnapshot] = useState<TailnetSnapshot | null>(null);
  const [snapshotLoading, setSnapshotLoading] = useState(false);
  const [selection, setSelection] = useState<string>(host);
  const [customHost, setCustomHost] = useState(host);
  const [probe, setProbe] = useState<ProbeStatus>("idle");
  const probeAbortRef = useRef<AbortController | null>(null);

  const pageHost = typeof window === "undefined" ? "localhost" : window.location.hostname || "localhost";

  const options = useMemo<BackendOption[]>(
    () => buildBackendOptions(pageHost, snapshot),
    [pageHost, snapshot],
  );

  const currentLabel = options.find((option) => option.url === host)?.label ?? host;

  useEffect(() => {
    if (!open) {
      return;
    }
    setSnapshotLoading(true);
    const controller = new AbortController();
    fetchBackendTailnetSnapshot(host, token, controller.signal)
      .then((fetched) => setSnapshot(fetched))
      .catch((error) => {
        if (isAuthError(error)) {
          onAuthFailure?.();
          return;
        }
        setSnapshot({ available: false, error: "discovery failed", peers: [] });
      })
      .finally(() => setSnapshotLoading(false));
    return () => controller.abort();
  }, [open, host, token, onAuthFailure]);

  useEffect(() => {
    if (!open) {
      return;
    }
    const matched = options.find((option) => option.url === host);
    if (matched) {
      setSelection(matched.url);
    } else {
      setSelection(CUSTOM_VALUE);
      setCustomHost(host);
    }
  }, [open, options, host]);

  const activeHost = selection === CUSTOM_VALUE ? customHost.trim() : selection;
  const dirty = Boolean(activeHost) && activeHost !== host;

  useEffect(() => {
    probeAbortRef.current?.abort();
    if (!open || !activeHost) {
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
  }, [activeHost, open]);

  if (!open) {
    return (
      <p className="meta backend-pill">
        Connected: <strong>{currentLabel}</strong>
        <button type="button" className="link-button" onClick={() => setOpen(true)}>
          change
        </button>
      </p>
    );
  }

  return (
    <section className="panel stack">
      <div className="field-row">
        <h3>Switch backend</h3>
        <button type="button" className="link-button" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
      <p className="muted">
        {snapshotLoading
          ? "Loading peers…"
          : snapshot?.available
            ? "Pick a different Tailscale peer or enter a custom URL. Switching forces a re-login."
            : "Tailscale not detected on the backend host. Use Custom URL…"}
      </p>
      <label className="field">
        <span>Backend</span>
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
          <span>Custom URL</span>
          <input
            value={customHost}
            onChange={(event) => setCustomHost(event.target.value)}
            placeholder="http://100.x.y.z:8787"
          />
        </label>
      ) : null}
      {activeHost ? <ProbeIndicator status={probe} url={activeHost} /> : null}
      <div className="action-row">
        <button
          type="button"
          className="primary"
          disabled={!dirty}
          onClick={() => onSwitch(activeHost)}
        >
          Switch
        </button>
      </div>
    </section>
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
