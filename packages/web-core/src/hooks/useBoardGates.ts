"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, fetchInboxItem, fetchManagers, fetchManagerState, isAuthError } from "../api";
import { managerGates, mergeGates, type BoardGate, type GateRead } from "../board";

const DEBOUNCE_MS = 300;
// A manager files its gate item before it records the item on the ticket, and a
// ticket change sends no frame: a later read catches up.
const SETTLE_MS = 10_000;
// Ticket changes send no frame, so listed gates are re-read on a timer.
const POLL_MS = 60_000;

async function gateWithItem(host: string, token: string, gate: BoardGate): Promise<BoardGate> {
  const id = gate.ticket.inbox_item_id;
  if (!id) return gate;
  try {
    return { ...gate, item: await fetchInboxItem(host, token, id) };
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return gate;
    throw error;
  }
}

// Each manager is read on its own, so one that fails hides no other's gates.
export async function loadGates(host: string, token: string): Promise<GateRead> {
  const managers = (await fetchManagers(host, token)).filter((manager) => manager.attention_count > 0);
  const reads = await Promise.allSettled(
    managers.map(async (manager) => {
      const gates = managerGates(manager, await fetchManagerState(host, token, manager.id));
      return Promise.all(gates.map((gate) => gateWithItem(host, token, gate)));
    }),
  );
  const rejected = reads.flatMap((read) => (read.status === "rejected" ? [read.reason] : []));
  const auth = rejected.find(isAuthError);
  if (auth) throw auth;
  return {
    gates: reads.flatMap((read) => (read.status === "fulfilled" ? read.value : [])),
    failed: managers.filter((_, index) => reads[index].status === "rejected").map((manager) => manager.id),
  };
}

export interface BoardGates {
  // Null until a read succeeds.
  gates: BoardGate[] | null;
  // The latest read did not reach every manager.
  failed: boolean;
  retry: () => void;
}

// The tickets waiting on the human across managers, each with its inbox item.
// Re-reads when `revision` moves and when the page is shown again.
// `onAuthFailure` must be stable: a new one drops the gates and reads again.
export function useBoardGates(
  host: string,
  token: string,
  revision: number,
  onAuthFailure: () => void,
): BoardGates {
  const [gates, setGates] = useState<BoardGate[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [tick, setTick] = useState(0);
  const read = useRef<(() => void) | null>(null);
  const queued = useRef<ReturnType<typeof setTimeout> | null>(null);

  const retry = useCallback(() => setTick((value) => value + 1), []);

  useEffect(() => {
    const bump = () => {
      if (document.visibilityState === "visible") setTick((value) => value + 1);
    };
    document.addEventListener("visibilitychange", bump);
    return () => document.removeEventListener("visibilitychange", bump);
  }, []);

  // Until every manager has been read, try again sooner.
  const interval = gates === null || failed ? SETTLE_MS : gates.length > 0 ? POLL_MS : null;
  useEffect(() => {
    if (interval === null) return;
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") setTick((value) => value + 1);
    }, interval);
    return () => clearInterval(timer);
  }, [interval]);

  // One reader per server. Reads may overlap: each that finishes is shown unless a
  // later one already is, so a busy board cannot keep every read from landing.
  useEffect(() => {
    setGates(null);
    setFailed(false);
    if (!host || !token) return;
    let active = true;
    let started = 0;
    let shown = 0;
    read.current = () => {
      const mine = ++started;
      loadGates(host, token)
        .then((next) => {
          if (!active || mine < shown) return;
          shown = mine;
          setGates((previous) => mergeGates(previous, next));
          setFailed(next.failed.length > 0);
        })
        .catch((error) => {
          if (!active || mine < shown) return;
          if (isAuthError(error)) onAuthFailure();
          else setFailed(true);
        });
    };
    return () => {
      active = false;
      read.current = null;
      if (queued.current !== null) clearTimeout(queued.current);
      queued.current = null;
    };
  }, [host, token, onAuthFailure]);

  useEffect(() => {
    // A change while a read is queued rides along with it, so changes that keep
    // coming cannot put it off.
    queued.current ??= setTimeout(() => {
      queued.current = null;
      read.current?.();
    }, DEBOUNCE_MS);
    const settled = setTimeout(() => read.current?.(), SETTLE_MS);
    return () => clearTimeout(settled);
  }, [host, token, revision, tick, onAuthFailure]);

  return { gates, failed, retry };
}
