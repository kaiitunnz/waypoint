"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  connectLaunchTarget,
  disconnectLaunchTarget,
  fetchLaunchTargetStatus,
  fetchMe,
  isAuthError,
} from "../api";
import { markTargetConnected, resolveActiveTargetId } from "../launchTargets";
import { readLaunchTarget, writeLaunchTarget } from "../store";
import type { LaunchTargetSummary } from "../types";

export interface SshConnectOutcome {
  connected: boolean;
  detail: string | null;
}

export interface LaunchTargetsState {
  targets: LaunchTargetSummary[];
  // The selected SSH launch target for this host; "" means the host itself.
  activeTargetId: string;
  selectTarget: (targetId: string) => void;
  connect: (targetId: string, password: string) => Promise<SshConnectOutcome>;
  disconnect: (targetId: string) => Promise<void>;
  error: string;
}

// SSH launch targets of the signed-in backend, the active one per host, and the
// password-auth connect flow. `onAuthFailure` must be stable.
export function useLaunchTargets(
  host: string,
  token: string,
  onAuthFailure?: () => void,
): LaunchTargetsState {
  const [targets, setTargets] = useState<LaunchTargetSummary[]>([]);
  const [activeTargetId, setActiveTargetId] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!host || !token) {
      return;
    }
    let active = true;
    fetchMe(host, token)
      .then((me) => {
        if (!active) return;
        setTargets(me.launch_targets);
        setActiveTargetId(resolveActiveTargetId(me.launch_targets, readLaunchTarget(host)));
      })
      .catch((fetchError) => {
        if (!active) return;
        if (isAuthError(fetchError)) onAuthFailure?.();
      });
    return () => {
      active = false;
      setTargets([]);
      setActiveTargetId("");
    };
  }, [host, token, onAuthFailure]);

  // A password-auth target may have lost its ControlMaster since the last
  // look; ask once when it becomes active.
  const activeAuth = targets.find((target) => target.id === activeTargetId)?.auth ?? null;
  useEffect(() => {
    if (!host || !token || !activeTargetId || activeAuth !== "password") {
      return;
    }
    let active = true;
    fetchLaunchTargetStatus(host, token, activeTargetId)
      .then((status) => {
        if (active) {
          setTargets((current) => markTargetConnected(current, activeTargetId, status.connected));
        }
      })
      .catch((statusError) => {
        if (active && isAuthError(statusError)) onAuthFailure?.();
      });
    return () => {
      active = false;
    };
  }, [host, token, activeTargetId, activeAuth, onAuthFailure]);

  const selectTarget = useCallback(
    (targetId: string) => {
      writeLaunchTarget(host, targetId);
      setActiveTargetId(targetId);
      setError("");
    },
    [host],
  );

  const connect = useCallback(
    async (targetId: string, password: string): Promise<SshConnectOutcome> => {
      try {
        const result = await connectLaunchTarget(host, token, targetId, password);
        setTargets((current) => markTargetConnected(current, targetId, result.connected));
        return { connected: result.connected, detail: result.detail ?? null };
      } catch (connectError) {
        if (isAuthError(connectError)) {
          onAuthFailure?.();
        }
        return {
          connected: false,
          detail: connectError instanceof Error ? connectError.message : "failed to connect",
        };
      }
    },
    [host, token, onAuthFailure],
  );

  const disconnect = useCallback(
    async (targetId: string) => {
      try {
        await disconnectLaunchTarget(host, token, targetId);
        setTargets((current) => markTargetConnected(current, targetId, false));
        setError("");
      } catch (disconnectError) {
        if (isAuthError(disconnectError)) {
          onAuthFailure?.();
          return;
        }
        setError(
          disconnectError instanceof Error ? disconnectError.message : "failed to disconnect",
        );
      }
    },
    [host, token, onAuthFailure],
  );

  return useMemo(
    () => ({ targets, activeTargetId, selectTarget, connect, disconnect, error }),
    [targets, activeTargetId, selectTarget, connect, disconnect, error],
  );
}
