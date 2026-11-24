"use client";

import { useEffect, useState } from "react";

import { fetchBoardChannels, fetchManagers, fetchManagerTickets, isAuthError } from "../api";
import { DEFAULT_TICKET_QUERY } from "../board";
import { summarizeChannels, type ChannelSummary } from "../usageSummary";

export interface TicketCounts {
  needYou: number;
  inFlight: number;
  blocked: number;
  merged: number;
}

// The manager's ticket board when one exists; otherwise the blackboard channels.
export type BoardSummary =
  | { kind: "tickets"; counts: TicketCounts }
  | { kind: "channels"; summary: ChannelSummary };

const REFRESH_DEBOUNCE_MS = 300;

async function loadBoardSummary(host: string, token: string): Promise<BoardSummary> {
  const managers = await fetchManagers(host, token);
  if (managers.length > 0) {
    const pages = await Promise.all(
      managers.map((manager) =>
        fetchManagerTickets(host, token, manager.id, DEFAULT_TICKET_QUERY, { limit: 1 }),
      ),
    );
    const counts = pages.reduce<TicketCounts>(
      (sum, { summary }) => ({
        needYou: sum.needYou + summary.awaiting_count,
        inFlight: sum.inFlight + summary.in_flight_count,
        blocked: sum.blocked + summary.blocked_count,
        merged: sum.merged + summary.merged_count,
      }),
      { needYou: 0, inFlight: 0, blocked: 0, merged: 0 },
    );
    return { kind: "tickets", counts };
  }
  return { kind: "channels", summary: summarizeChannels(await fetchBoardChannels(host, token)) };
}

// Refetches when `boardRevision` moves (a `board_update` frame), settled so a
// burst of posts costs one request. `onAuthFailure` must be stable.
export function useBoardSummary(
  host: string,
  token: string,
  boardRevision: number,
  onAuthFailure?: () => void,
): BoardSummary | null {
  const [summary, setSummary] = useState<BoardSummary | null>(null);

  useEffect(() => {
    if (!host || !token) return;
    let active = true;
    const timer = setTimeout(
      () => {
        loadBoardSummary(host, token)
          .then((next) => {
            if (active) setSummary(next);
          })
          .catch((error) => {
            if (active && isAuthError(error)) onAuthFailure?.();
          });
      },
      boardRevision === 0 ? 0 : REFRESH_DEBOUNCE_MS,
    );
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [host, token, boardRevision, onAuthFailure]);

  return summary;
}
