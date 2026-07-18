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
  sameBackendUrl,
  TailnetSnapshot,
} from "@/lib/tailnet";

interface BackendSwitcherProps {
  host: string;
  token: string;
  launchTargets: LaunchTargetSummary[];
  targetId: string;
  onSwitch: (nextHost: string, nextTargetId: string) => void;
  onConnectTarget: (target: LaunchTargetSummary) => void;
  onDisconnectTarget: (targetId: string) => void;
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

export function BackendSwitcher({ host, token, launchTargets, targetId, onSwitch, onConnectTarget, onDisconnectTarget, onAuthFailure }: BackendSwitcherProps) {
  const [open, setOpen] = useState(false);
  const [snapshot, setSnapshot] = useState<TailnetSnapshot | null>(null);
  const [snapshotLoading, setSnapshotLoading] = useState(false);
  const [selection, setSelection] = useState<string>(host);
  const [customHost, setCustomHost] = useState(host);
  const [probe, setProbe] = useState<ProbeStatus>("idle");
  const probeAbortRef = useRef<AbortController | null>(null);
  const onAuthFailureRef = useRef(onAuthFailure);
  useEffect(() => { onAuthFailureRef.current = onAuthFailure; });
  // Reset each time the panel opens so a stale snapshot or a re-rendered prop
  // can't override the choice the user makes while the panel is open.
  const userTouchedRef = useRef(false);

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

  const activeTarget = launchTargets.find((target) => target.id === targetId) ?? null;

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
    userTouchedRef.current = false;
  }, [open]);

  useEffect(() => {
    if (!open || userTouchedRef.current) {
      return;
    }
    if (targetId && launchTargets.some((target) => target.id === targetId)) {
      setSelection(`${TARGET_PREFIX}${targetId}`);
      return;
    }
    const matched = hostOptions.find((option) => sameBackendUrl(option.url, host));
    if (matched) {
      setSelection(matched.url);
      return;
    }
    setSelection(CUSTOM_VALUE);
    setCustomHost(host);
  }, [open, hostOptions, host, targetId, launchTargets]);

  const selectedTargetId = selection.startsWith(TARGET_PREFIX) ? selection.slice(TARGET_PREFIX.length) : "";
  const selectedTarget = launchTargets.find((target) => target.id === selectedTargetId) ?? null;
  const activeHost = selection === CUSTOM_VALUE || selection.startsWith(TARGET_PREFIX) ? customOrCurrentHost(selection, customHost, host) : selection;
  const dirty = !sameBackendUrl(activeHost, host) || selectedTargetId !== targetId;

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

  // Dismiss the popover on outside-click or Escape, returning focus to the
  // trigger chip so keyboard users don't lose their place.
  const wrapRef = useRef<HTMLSpanElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    if (!open) {
      return;
    }
    const onPointerDown = (event: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const { chipName, chipUrl } = describeChip(host, activeTarget);
  const chipConnected =
    activeTarget?.auth === "password" ? Boolean(activeTarget.connected) : true;

  return (
    <span className="hostctl-wrap" ref={wrapRef}>
      <button
        type="button"
        className="hostctl"
        ref={triggerRef}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        <span
          className={`hostctl-dot${chipConnected ? "" : " is-off"}`}
          aria-hidden="true"
        />
        <span className="hostctl-name">{chipName}</span>
        {chipUrl ? <span className="hostctl-url">· {chipUrl}</span> : null}
        <span className="hostctl-caret" aria-hidden="true">
          ▾
        </span>
      </button>
      {open ? (
        <div className="backend-popover" role="dialog" aria-label="Switch backend">
          <div className="backend-popover-head">
            <h4>Switch backend</h4>
            <button
              type="button"
              className="link-button"
              onClick={() => setOpen(false)}
            >
              Close
            </button>
          </div>
          <p className="backend-popover-hint">
            {snapshotLoading
              ? "Loading peers…"
              : snapshot?.available
                ? "Pick a Tailscale peer, an SSH coding target, or a custom URL. Changing host forces a re-login."
                : "Tailscale not detected on the backend host. You can still switch to an SSH coding target or use Custom URL…"}
          </p>
          <label className="field">
            <span>Backend</span>
            <select
              value={selection}
              onChange={(event) => {
                userTouchedRef.current = true;
                setSelection(event.target.value);
              }}
            >
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
          {selectedTarget?.auth === "password" ? (
            <SshAuthControl
              target={selectedTarget}
              onConnect={onConnectTarget}
              onDisconnect={onDisconnectTarget}
            />
          ) : null}
          <button
            type="button"
            className="backend-popover-apply"
            disabled={!dirty}
            onClick={() => onSwitch(activeHost, selectedTargetId)}
          >
            Apply
          </button>
        </div>
      ) : null}
    </span>
  );
}

// A compact chip label for the app-bar host control: the SSH target name when
// one is active, otherwise a friendly host name derived from the URL. The
// separate URL fragment shows host:port so the raw endpoint stays visible.
function describeChip(
  host: string,
  activeTarget: LaunchTargetSummary | null,
): { chipName: string; chipUrl: string } {
  let hostname = host;
  let port = "";
  try {
    const url = new URL(host);
    hostname = url.hostname;
    port = url.port;
  } catch {
    // Leave the raw host as the name if it is not a parseable URL.
  }
  const isLocal = hostname === "localhost" || hostname === "127.0.0.1";
  const shortHost = isLocal ? "Local" : hostname.split(".")[0] || hostname;
  const chipUrl = port ? `${hostname}:${port}` : hostname;
  if (activeTarget) {
    return { chipName: activeTarget.name, chipUrl };
  }
  return { chipName: shortHost, chipUrl };
}

function SshAuthControl({
  target,
  onConnect,
  onDisconnect,
}: {
  target: LaunchTargetSummary;
  onConnect: (target: LaunchTargetSummary) => void;
  onDisconnect: (targetId: string) => void;
}) {
  const connected = Boolean(target.connected);
  return (
    <div className={`ssh-auth-row${connected ? " is-connected" : ""}`}>
      <span className="ssh-auth-dot" aria-hidden="true" />
      <span className="ssh-auth-label">
        {connected ? "SSH connected" : "SSH password required"}
      </span>
      {connected ? (
        <button
          type="button"
          className="link-button"
          onClick={() => onDisconnect(target.id)}
        >
          Disconnect
        </button>
      ) : (
        <button
          type="button"
          className="secondary ssh-auth-connect"
          onClick={() => onConnect(target)}
        >
          Connect
        </button>
      )}
    </div>
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
