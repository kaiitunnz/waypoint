"use client";

import { useEffect, useRef, useState } from "react";

import { ApiError, fetchInboxItem, fetchManagers, fetchManagerState, isAuthError } from "../api";
import { managerGates, type BoardGate } from "../board";

const DEBOUNCE_MS = 300;
// A manager files its gate item before it records the item on the ticket, and a
// ticket change sends no frame of its own: read once more after the dust settles,
// and keep an eye on the gates that exist.
const SETTLE_MS = 10_000;
const POLL_MS = 60_000;

async function loadGates(host: string, token: string): Promise<BoardGate[]> {
  const managers = (await fetchManagers(host, token)).filter((manager) => manager.attention_count > 0);
  const states = await Promise.all(
    managers.map(async (manager) => managerGates(manager, await fetchManagerState(host, token, manager.id))),
  );
  return Promise.all(
    states.flat().map(async (gate) => {
      const id = gate.ticket.inbox_item_id;
      if (!id) return gate;
      try {
        return { ...gate, item: await fetchInboxItem(host, token, id) };
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return gate;
        throw error;
      }
    }),
  );
}

// The tickets waiting on the human across managers, each with its inbox item.
// Null until the first read. Re-reads when `revision` moves and when the page is
// shown again. `onAuthFailure` must be stable.
export function useBoardGates(
  host: string,
  token: string,
  revision: number,
  onAuthFailure?: () => void,
): BoardGate[] | null {
  const [gates, setGates] = useState<BoardGate[] | null>(null);
  const [tick, setTick] = useState(0);
  const read = useRef<(() => void) | null>(null);
  const queued = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const bump = () => {
      if (document.visibilityState === "visible") setTick((value) => value + 1);
    };
    document.addEventListener("visibilitychange", bump);
    return () => document.removeEventListener("visibilitychange", bump);
  }, []);

  // Until a first read lands, try again sooner.
  const interval = gates === null ? SETTLE_MS : gates.length > 0 ? POLL_MS : null;
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
          setGates(next);
        })
        .catch((error) => {
          if (active && isAuthError(error)) onAuthFailure?.();
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

  return gates;
}
