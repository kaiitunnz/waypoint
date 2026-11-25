"use client";

import { useCallback, useState } from "react";

import { isAuthError, setSessionEffort, setSessionModel, setSessionPermissionMode } from "../api";
import type { BackendCatalog } from "../backends";
import { changeInterruptsTurn, type TuningField } from "../session/tuning";
import type { SessionRecord } from "../types";

const FIELD_NAMES: Record<TuningField, string> = { mode: "permission mode", model: "model", effort: "effort" };

// The question to put to the person before a change cuts off the running turn.
export function interruptPrompt(field: string): string {
  return `Changing the ${field} restarts this session and interrupts the current turn. Continue?`;
}

// `onSession`, `onError`, `onAuthFailure` and `confirmInterrupt` must be stable:
// the returned changes keep their identity until the session does.
export interface SessionTuningOptions {
  host: string;
  token: string;
  session: SessionRecord | null;
  catalog: BackendCatalog;
  onSession: (session: SessionRecord) => void;
  // Called with "" as a change starts, to clear the last failure.
  onError: (message: string) => void;
  onAuthFailure: () => void;
  confirmInterrupt: (field: string) => boolean | Promise<boolean>;
}

export interface SessionTuningActions {
  busy: Record<TuningField, boolean>;
  // `confirmed` skips the interrupt question.
  changeMode: (mode: string, confirmed?: boolean) => Promise<void>;
  changeModel: (model: string, confirmed?: boolean) => Promise<void>;
  changeEffort: (effort: string, confirmed?: boolean) => Promise<void>;
}

export function useSessionTuning({
  host,
  token,
  session,
  catalog,
  onSession,
  onError,
  onAuthFailure,
  confirmInterrupt,
}: SessionTuningOptions): SessionTuningActions {
  const [busy, setBusy] = useState<Record<TuningField, boolean>>({ mode: false, model: false, effort: false });

  const change = useCallback(
    async (
      field: TuningField,
      confirmed: boolean,
      current: SessionRecord,
      request: () => Promise<SessionRecord>,
      fallback: string,
    ) => {
      if (!confirmed && changeInterruptsTurn(current, catalog) && !(await confirmInterrupt(FIELD_NAMES[field]))) {
        return;
      }
      setBusy((value) => ({ ...value, [field]: true }));
      onError("");
      try {
        onSession(await request());
      } catch (error) {
        if (isAuthError(error)) {
          onAuthFailure();
          return;
        }
        onError(error instanceof Error ? error.message : fallback);
      } finally {
        setBusy((value) => ({ ...value, [field]: false }));
      }
    },
    [catalog, confirmInterrupt, onAuthFailure, onError, onSession],
  );

  const changeMode = useCallback(
    async (mode: string, confirmed = false) => {
      if (!session || mode === (session.permission_mode ?? "default")) return;
      await change(
        "mode",
        confirmed,
        session,
        () => setSessionPermissionMode(host, token, session.id, mode),
        "failed to update mode",
      );
    },
    [change, host, token, session],
  );

  const changeModel = useCallback(
    async (model: string, confirmed = false) => {
      const cleaned = model.trim() || null;
      if (!session || cleaned === (session.model ?? null)) return;
      await change(
        "model",
        confirmed,
        session,
        () => setSessionModel(host, token, session.id, cleaned),
        "failed to update model",
      );
    },
    [change, host, token, session],
  );

  const changeEffort = useCallback(
    async (effort: string, confirmed = false) => {
      const cleaned = effort.trim() || null;
      if (!session || cleaned === (session.effort ?? null)) return;
      await change(
        "effort",
        confirmed,
        session,
        () => setSessionEffort(host, token, session.id, cleaned),
        "failed to update effort",
      );
    },
    [change, host, token, session],
  );

  return { busy, changeMode, changeModel, changeEffort };
}
