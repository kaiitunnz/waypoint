"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { isAuthError, probeBackend } from "@/lib/api";
import { LaunchTargetSummary } from "@/lib/types";
import {
  backendPortFromUrl,
  BackendOption,
  buildBackendOptions,
  DEFAULT_BACKEND_PORT,
  fetchBackendTailnetSnapshot,
  TailnetSnapshot,
} from "@/lib/tailnet";

interface BackendSwitcherProps {
  host: string;
  token: string;
  launchTargets: LaunchTargetSummary[];
  targetId: string;
  onSwitch: (nextHost: string, nextTargetId: string) => void;
  onAuthFailure?: () => void;
}

const CUSTOM_VALUE = "__custom__";
const TARGET_PREFIX = "__target__:";
type ProbeStatus = "idle" | "checking" | "reachable" | "unreachable";

interface PickerOption {
  value: string;
  label: string;
  hint: string | null;
}

export function BackendSwitcher({ host, token, launchTargets, targetId, onSwitch, onAuthFailure }: BackendSwitcherProps) {
  const [open, setOpen] = useState(false);
  const [snapshot, setSnapshot] = useState<TailnetSnapshot | null>(null);
  const [snapshotLoading, setSnapshotLoading] = useState(false);
  const [selection, setSelection] = useState<string>(host);
  const [customHost, setCustomHost] = useState(host);
  const [probe, setProbe] = useState<ProbeStatus>("idle");
  const probeAbortRef = useRef<AbortController | null>(null);
  const onAuthFailureRef = useRef(onAuthFailure);
  useEffect(() => { onAuthFailureRef.current = onAuthFailure; });

  const pageHost = typeof window === "undefined" ? "localhost" : window.location.hostname || "localhost";

  const backendPort = backendPortFromUrl(host) ?? DEFAULT_BACKEND_PORT;
  const hostOptions = useMemo<BackendOption[]>(
    () => buildBackendOptions(pageHost, snapshot, backendPort),
    [pageHost, snapshot, backendPort],
  );

  const pickerOptions = useMemo<PickerOption[]>(
    () => [
      ...hostOptions.map((option) => ({ value: option.url, label: option.label, hint: option.hint })),
      ...launchTargets.map((target) => ({
        value: `${TARGET_PREFIX}${target.id}`,
        label: `SSH: ${target.name}`,
        hint: target.supported_backends.join(", "),
      })),
    ],
    [hostOptions, launchTargets],
  );

  const hostLabel = hostOptions.find((option) => option.url === host)?.label ?? host;
  const activeTarget = launchTargets.find((target) => target.id === targetId) ?? null;
  const currentLabel = activeTarget ? `${hostLabel} / SSH: ${activeTarget.name}` : hostLabel;

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
          onAuthFailureRef.current?.();
          return;
        }
        setSnapshot({ available: false, error: "discovery failed", peers: [] });
      })
      .finally(() => setSnapshotLoading(false));
    return () => controller.abort();
  }, [open, host, token]);

  useEffect(() => {
    if (!open) {
      return;
    }
    if (targetId && launchTargets.some((target) => target.id === targetId)) {
      setSelection(`${TARGET_PREFIX}${targetId}`);
      return;
    }
    const matched = hostOptions.find((option) => option.url === host);
    if (matched) {
      setSelection(matched.url);
      return;
    }
    setSelection(CUSTOM_VALUE);
    setCustomHost(host);
  }, [open, hostOptions, host, targetId, launchTargets]);

  const selectedTargetId = selection.startsWith(TARGET_PREFIX) ? selection.slice(TARGET_PREFIX.length) : "";
  const activeHost = selection === CUSTOM_VALUE || selection.startsWith(TARGET_PREFIX) ? customOrCurrentHost(selection, customHost, host) : selection;
  const dirty = activeHost !== host || selectedTargetId !== targetId;

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
            ? "Pick a different Tailscale peer, switch to an SSH coding target, or enter a custom URL. Changing host forces a re-login."
            : "Tailscale not detected on the backend host. You can still switch to an SSH coding target or use Custom URL…"}
      </p>
      <label className="field">
        <span>Backend</span>
        <select value={selection} onChange={(event) => setSelection(event.target.value)}>
          {pickerOptions.map((option) => (
            <option key={option.value} value={option.value}>
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
          onClick={() => onSwitch(activeHost, selectedTargetId)}
        >
          Apply
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

function customOrCurrentHost(selection: string, customHost: string, host: string): string {
  if (selection === CUSTOM_VALUE) {
    return customHost.trim();
  }
  return host;
}
