"use client";

import { useEffect, useState } from "react";

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

  useEffect(() => {
    const bump = () => {
      if (document.visibilityState === "visible") setTick((value) => value + 1);
    };
    document.addEventListener("visibilitychange", bump);
    return () => document.removeEventListener("visibilitychange", bump);
  }, []);

  const watching = (gates?.length ?? 0) > 0;
  useEffect(() => {
    if (!watching) return;
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") setTick((value) => value + 1);
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [watching]);

  useEffect(() => {
    setGates(null);
  }, [host, token]);

  useEffect(() => {
    if (!host || !token) return;
    let active = true;
    // The latest read wins when a slow one resolves late.
    let latest = 0;
    const read = () => {
      const mine = ++latest;
      loadGates(host, token)
        .then((next) => {
          if (active && mine === latest) setGates(next);
        })
        .catch((error) => {
          if (active && isAuthError(error)) onAuthFailure?.();
        });
    };
    const soon = setTimeout(read, DEBOUNCE_MS);
    const settled = setTimeout(read, SETTLE_MS);
    return () => {
      active = false;
      clearTimeout(soon);
      clearTimeout(settled);
    };
  }, [host, token, revision, tick, onAuthFailure]);

  return gates;
}
